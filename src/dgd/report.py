"""Write scan results in formats people actually open.

GeoJSON and GeoTIFF for the GIS crowd, KML for Google Earth and phones, CSV
for the spreadsheet crowd, PNG clips for humans, one HTML page that ties it
all together, and a manifest that records exactly how every number was made.
If a lead ever turns into a case, somebody is going to ask. Better to have
the answer written down than to reconstruct it from memory in front of a
lawyer.
"""

from __future__ import annotations

import csv
import hashlib
import html
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from dgd import __version__  # noqa: E402
from dgd.anomaly import COMPONENTS  # noqa: E402
from dgd.candidates import chip_window  # noqa: E402
from dgd.landcover import WORLDCOVER_CITATION  # noqa: E402
from dgd.scan import ScanResult  # noqa: E402

DISCLAIMER = (
    "These are leads, not evidence. Each one marks a patch of ground that changed in a way "
    "consistent with dumping, burning or excavation, compared to the same season in previous "
    "years. Farming, construction, storms and plain bad luck produce the same signals. Every "
    "lead needs to be checked by a person, in higher resolution imagery or on the ground, "
    "before anybody acts on it."
)

KIND_COLORS = {"persistent": "#d7191c", "transient": "#fdae61"}
KML_COLORS = {"persistent": "ff1c19d7", "transient": "ff61aefd"}  # aabbggrr, because KML

RASTERS = {
    "score.tif": ("score", "Persistent change score (robust z, median over target window)"),
    "persistence.tif": ("persistence", "Fraction of clear target passes above the score threshold"),
    "event.tif": ("event", "Transient dig event strength (robust z, sustained at least the minimum span)"),
}


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else ""


def _ascii(text: str) -> str:
    """Belt and braces. Anything non-ASCII that sneaks in gets replaced."""
    return text.encode("ascii", "replace").decode("ascii")


def candidate_rows(result: ScanResult) -> list[dict]:
    rows = []
    for c in result.candidates:
        rows.append(
            {
                "id": c.id,
                "rank": c.rank,
                "kind": c.kind,
                "signature": c.signature,
                "lat": round(c.lat, 6),
                "lon": round(c.lon, 6),
                "area_m2": round(c.area_m2),
                "pixels": c.n_px,
                "score_mean": round(c.score_mean, 2),
                "score_max": round(c.score_max, 2),
                "persistence": round(c.persistence, 2),
                "landcover": c.landcover_name,
                "field_like": c.field_like,
                "last_clear_look": _fmt_date(c.last_clear),
                "first_seen": _fmt_date(c.first_seen),
                "last_seen": _fmt_date(c.last_seen),
                **{k: round(c.components[k], 2) for k in COMPONENTS},
                "rank_score": round(c.rank_score, 2),
                "maps_url": c.maps_url,
            }
        )
    return rows


def write_rasters(result: ScanResult, out: Path) -> list[Path]:
    import rasterio

    grid = result.config.grid
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": 1,
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": float("nan"),
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    if grid.width < 256 or grid.height < 256:
        profile.update(tiled=False)
        profile.pop("blockxsize")
        profile.pop("blockysize")
    paths = []
    for name, (key, desc) in RASTERS.items():
        path = out / name
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(result.rasters[key].astype(np.float32), 1)
            dst.set_band_description(1, desc)
        paths.append(path)
    path = out / "candidates_mask.tif"
    p2 = {**profile, "dtype": "int32", "nodata": 0}
    with rasterio.open(path, "w", **p2) as dst:
        dst.write(result.labels.astype(np.int32), 1)
        dst.set_band_description(1, "Candidate label raster (see candidates.geojson 'label')")
    paths.append(path)
    return paths


def write_vectors(result: ScanResult, out: Path) -> list[Path]:
    import geopandas as gpd

    rows = candidate_rows(result)
    gj = out / "candidates.geojson"
    if rows:
        gdf = gpd.GeoDataFrame(
            [{**r, "label": c.label} for r, c in zip(rows, result.candidates)],
            geometry=[c.geometry for c in result.candidates],
            crs=result.config.grid.crs,
        ).to_crs("EPSG:4326")
        gdf.to_file(gj, driver="GeoJSON")
    else:
        gj.write_text('{"type": "FeatureCollection", "features": []}\n', encoding="ascii")

    csv_path = out / "candidates.csv"
    fields = list(rows[0].keys()) if rows else ["id"]
    with csv_path.open("w", newline="", encoding="ascii", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    return [gj, csv_path, write_kml(result, out / "candidates.kml")]


def _kml_coords(ring) -> str:
    return " ".join(f"{x:.7f},{y:.7f},0" for x, y in ring.coords)


def write_kml(result: ScanResult, path: Path) -> Path:
    import shapely
    from pyproj import Transformer
    from shapely.geometry import MultiPolygon

    tr = Transformer.from_crs(result.config.grid.crs, "EPSG:4326", always_xy=True)

    def to_wgs84(geom):
        return shapely.transform(geom, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))

    name = escape(_ascii(result.config.name or "Disturbance scan"))
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
        f"<name>{name}</name>",
    ]
    for kind, color in KML_COLORS.items():
        parts.append(
            f'<Style id="{kind}"><LineStyle><color>{color}</color><width>3</width></LineStyle>'
            f"<PolyStyle><fill>0</fill></PolyStyle><IconStyle><color>{color}</color></IconStyle></Style>"
        )
    for c, row in zip(result.candidates, candidate_rows(result)):
        geom = to_wgs84(c.geometry)
        polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
        desc = "<br/>".join(f"{escape(k)}: {escape(str(v))}" for k, v in row.items() if k != "maps_url")
        desc += f'<br/><a href="{escape(c.maps_url)}">Open in Google Maps</a>'
        parts.append(f"<Placemark><name>{c.id} ({c.kind})</name><styleUrl>#{c.kind}</styleUrl>")
        parts.append(f"<description><![CDATA[{_ascii(desc)}]]></description><MultiGeometry>")
        parts.append(f"<Point><coordinates>{c.lon:.7f},{c.lat:.7f},0</coordinates></Point>")
        for poly in polys:
            parts.append(f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{_kml_coords(poly.exterior)}</coordinates></LinearRing></outerBoundaryIs>")
            for hole in poly.interiors:
                parts.append(f"<innerBoundaryIs><LinearRing><coordinates>{_kml_coords(hole)}</coordinates></LinearRing></innerBoundaryIs>")
            parts.append("</Polygon>")
        parts.append("</MultiGeometry></Placemark>")
    parts.append("</Document></kml>")
    path.write_text("\n".join(parts) + "\n", encoding="ascii")
    return path


def _stretch(rgb: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    scaled = (rgb - lo[:, None, None]) / (hi - lo + 1e-6)[:, None, None]
    img = np.clip(np.moveaxis(scaled, 0, -1), 0, 1)
    img[~np.isfinite(img)] = 0
    return img


def _shared_stretch(*stacks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """One stretch for before and after. Separate stretches make anything look like change."""
    joined = np.concatenate([s.reshape(3, -1) for s in stacks], axis=1)
    lo = np.array([np.nanpercentile(b, 2) if np.isfinite(b).any() else 0.0 for b in joined])
    hi = np.array([np.nanpercentile(b, 98) if np.isfinite(b).any() else 1.0 for b in joined])
    return lo, hi


def _outline(ax, geom, color, lw=1.6):
    polys = list(getattr(geom, "geoms", [geom]))
    for poly in polys:
        x, y = poly.exterior.xy
        ax.plot(x, y, color=color, linewidth=lw)


def write_chips(result: ScanResult, out: Path) -> list[Path]:
    """Before / after / score panels plus the score history, one PNG per lead."""
    cfg = result.config
    grid = cfg.grid
    chips_dir = out / "chips"
    chips_dir.mkdir(exist_ok=True)
    paths = []
    left, _, _, top = grid.bounds
    res = grid.res
    for c in result.candidates[: cfg.max_chips]:
        rs, cs = chip_window(c, grid.shape)
        r0, r1, c0, c1 = rs.start, rs.stop, cs.start, cs.stop
        before = result.rasters["rgb_baseline"][:, r0:r1, c0:c1]
        if c.after_rgb is not None and c.after_rgb.shape[1:] == before.shape[1:]:
            after, after_title = c.after_rgb, c.after_label
        else:
            after, after_title = result.rasters["rgb_target"][:, r0:r1, c0:c1], f"After: median {cfg.target_start} to {cfg.target_end}"
        lo, hi = _shared_stretch(before, after)
        key = "score" if c.kind == "persistent" else "event"
        heat = result.rasters[key][r0:r1, c0:c1]
        extent = [left + c0 * res, left + c1 * res, top - r1 * res, top - r0 * res]
        color = KIND_COLORS[c.kind]

        fig = plt.figure(figsize=(12, 6.2))
        gs = fig.add_gridspec(2, 3, height_ratios=[3, 1.3])
        panels = [
            (before, f"Before: median of same season, {cfg.baseline_years} prior years"),
            (after, after_title),
        ]
        for i, (img, title) in enumerate(panels):
            ax = fig.add_subplot(gs[0, i])
            ax.imshow(_stretch(img, lo, hi), extent=extent)
            _outline(ax, c.geometry, color)
            ax.set_title(title, fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
        ax = fig.add_subplot(gs[0, 2])
        im = ax.imshow(heat, extent=extent, cmap="magma", vmin=0, vmax=max(8.0, float(np.nanmax(heat)) if np.isfinite(heat).any() else 8.0))
        _outline(ax, c.geometry, "cyan", 1.2)
        ax.set_title("Change score (robust z)" if c.kind == "persistent" else "Dig event strength (robust z)", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)

        ax = fig.add_subplot(gs[1, :])
        d = [x for x, _ in c.timeseries]
        v = [y for _, y in c.timeseries]
        ax.plot(d, v, "o-", color="#555555", markersize=3, linewidth=0.8)
        ax.axhline(cfg.min_score if c.kind == "persistent" else cfg.min_event_score, color=color, linestyle="--", linewidth=1)
        ax.axvspan(cfg.target_start, cfg.target_end, color=color, alpha=0.08)
        if c.first_seen:
            ax.axvline(c.first_seen, color=color, linewidth=1)
        ax.set_ylabel("score", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.set_title("Score inside the outline, every clear pass (shaded = target window, dashed = threshold)", fontsize=8)

        when = ""
        if c.first_seen:
            when = f" | appeared between {_fmt_date(c.last_clear) or 'start of record'} and {c.first_seen}"
        fig.suptitle(
            _ascii(
                f"{c.id} | {c.kind} ({c.signature}) | {c.lat:.5f}, {c.lon:.5f} | {c.area_m2:,.0f} m2 | "
                f"score {c.score_mean:.1f} | {c.landcover_name}{when}"
            ),
            fontsize=10,
        )
        fig.tight_layout()
        path = chips_dir / f"{c.id}.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        paths.append(path)
    return paths


def write_overview(result: ScanResult, out: Path, max_labels: int = 40) -> Path:
    grid = result.config.grid
    left, bottom, right, top = grid.bounds
    after = result.rasters["rgb_target"]
    lo, hi = _shared_stretch(after)
    fig, ax = plt.subplots(figsize=(9, 9 * grid.height / max(grid.width, 1) + 0.6))
    ax.imshow(_stretch(after, lo, hi), extent=[left, right, bottom, top])
    for c in result.candidates[:max_labels]:
        _outline(ax, c.geometry, KIND_COLORS[c.kind], 1.4)
        ax.annotate(c.id, (c.geometry.centroid.x, c.geometry.centroid.y), color="white", fontsize=7,
                    xytext=(4, 4), textcoords="offset points",
                    bbox=dict(facecolor="black", alpha=0.6, edgecolor="none", pad=1))
    ax.set_title(_ascii(f"{result.config.name or 'Scan'}: {len(result.candidates)} leads "
                        f"(red persistent, orange transient), after composite"), fontsize=10)
    ax.set_xlabel(f"Easting (m, {grid.crs})", fontsize=8)
    ax.set_ylabel(f"Northing (m, {grid.crs})", fontsize=8)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    path = out / "overview.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def write_html(result: ScanResult, out: Path) -> Path:
    cfg = result.config
    e = lambda x: html.escape(_ascii(str(x)))  # noqa: E731
    rows = candidate_rows(result)
    lon, lat = cfg.grid.center_lonlat()
    body = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(cfg.name or 'Disturbance scan')}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:1300px;color:#222;background:#fff}"
        "table{border-collapse:collapse;font-size:13px;width:100%}th,td{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left;vertical-align:top}"
        "th{background:#f3f3f3;position:sticky;top:0}.persistent{color:#b2181b;font-weight:600}.transient{color:#c66a00;font-weight:600}"
        ".note{background:#fff7e6;border-left:4px solid #f0a020;padding:8px 12px;margin:12px 0}img{max-width:100%}"
        "details{margin:6px 0}code{background:#f3f3f3;padding:1px 4px}</style></head><body>",
        f"<h1>{e(cfg.name or 'Disturbance scan')}</h1>",
        f"<p>Area centre {lat:.5f}, {lon:.5f} | {cfg.grid.width} x {cfg.grid.height} pixels at {cfg.grid.res:.0f} m | "
        f"target window {cfg.target_start} to {cfg.target_end} | baseline: same season, {cfg.baseline_years} prior years | "
        f"{len(result.baseline)} baseline and {len(result.target)} target passes used</p>",
        f"<div class='note'><b>Read this first.</b> {e(DISCLAIMER)}</div>",
        "<p><b>persistent</b>: the change showed up and stayed (surface dumping, fill, burning, clearing). "
        "<b>transient</b>: bare soil appeared for at least "
        f"{cfg.min_event_span_days} days and then went away or the window ended (dig and cover, plowing, works).</p>",
        "<p><img src='overview.png' alt='overview map'></p>",
        "<p>Leads on cropland or shaped like a whole field are marked <i>farming?</i> and ranked lower. They are not removed, "
        "because burying waste in farmland happens. They are just less likely to be anything but a tractor.</p>",
        "<table><tr><th>ID</th><th>Kind</th><th>Where</th><th>Area m2</th><th>Score</th><th>Persist.</th><th>Land cover</th><th>Appeared between</th></tr>",
    ]
    for r in rows:
        when = f"{r['last_clear_look'] or '?'} and {r['first_seen'] or '?'}"
        if r["last_seen"]:
            when += f" (last bare {r['last_seen']})"
        body.append(
            f"<tr><td><a href='#{e(r['id'])}'>{e(r['id'])}</a></td><td class='{e(r['kind'])}'>{e(r['kind'])} ({e(r['signature'])})</td>"
            f"<td><a href='{e(r['maps_url'])}'>{r['lat']:.5f}, {r['lon']:.5f}</a></td><td>{r['area_m2']:,}</td>"
            f"<td>{r['score_mean']}</td><td>{r['persistence']}</td><td>{e(r['landcover'])}{' <i>farming?</i>' if (r['field_like'] or r['landcover'] == 'cropland') else ''}</td><td>{e(when)}</td></tr>"
        )
    body.append("</table>")
    if not rows:
        body.append("<p>No leads above the thresholds. Either the area is clean or the thresholds are too strict. "
                    "Both happen.</p>")
    for c in result.candidates[: cfg.max_chips]:
        body.append(f"<h3 id='{e(c.id)}'>{e(c.id)}</h3><img src='chips/{e(c.id)}.png' alt='{e(c.id)}' loading='lazy'>")
    if len(result.candidates) > cfg.max_chips:
        body.append(f"<p>Only the top {cfg.max_chips} leads have image clips. The rest are in the CSV, GeoJSON and KML.</p>")
    body.append("<h2>Files</h2><ul>"
                "<li><code>candidates.csv</code>, <code>candidates.geojson</code>, <code>candidates.kml</code>: the leads</li>"
                "<li><code>score.tif</code>, <code>persistence.tif</code>, <code>event.tif</code>, <code>candidates_mask.tif</code>: rasters</li>"
                "<li><code>manifest.json</code>: every scene, parameter and file hash used to make this report</li></ul>")
    body.append(f"<p style='color:#777;font-size:12px'>Generated by Soil-Disturbance-Detector {e(__version__)}. "
                f"Contains modified Copernicus Sentinel data {result.target[-1].date.year if result.target else ''}. "
                f"Land cover: {e(WORLDCOVER_CITATION)}.</p></body></html>")
    path = out / "report.html"
    path.write_text("\n".join(body) + "\n", encoding="ascii")
    return path


def _git_commit() -> str | None:
    try:
        here = Path(__file__).resolve().parent
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=here, capture_output=True, text=True, timeout=5)
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=here, capture_output=True, text=True, timeout=5)
        if commit.returncode != 0:
            return None
        return commit.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except Exception:
        return None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _passes(acqs) -> list[dict]:
    return [
        {
            "pass": a.key,
            "date": a.date.isoformat(),
            "granules": [{"id": s.id, "collection": s.collection, "cloud_cover": s.cloud_cover} for s in a.scenes],
        }
        for a in acqs
    ]


def write_manifest(result: ScanResult, out: Path, files: list[Path]) -> Path:
    cfg = result.config
    from dgd.anomaly import seasonal_windows

    manifest = {
        "tool": "Soil-Disturbance-Detector (dgd)",
        "version": __version__,
        "git_commit": _git_commit(),
        "command": [_ascii(a) for a in sys.argv],
        "started_utc": result.started.isoformat(),
        "finished_utc": result.finished.isoformat() if result.finished else None,
        "aoi": {
            "name": _ascii(cfg.name),
            "crs": cfg.grid.crs,
            "transform": list(cfg.grid.transform)[:6],
            "width": cfg.grid.width,
            "height": cfg.grid.height,
            "lonlat_bbox": cfg.grid.lonlat_bbox(),
            "masked_by_polygon": cfg.aoi_mask is not None,
        },
        "windows": {
            "target": [cfg.target_start.isoformat(), cfg.target_end.isoformat()],
            "baseline": [[s.isoformat(), e.isoformat()] for s, e in seasonal_windows(cfg.target_start, cfg.target_end, cfg.baseline_years)],
        },
        "parameters": {
            k: (list(v) if isinstance(v, tuple) else v)
            for k, v in vars(cfg).items()
            if isinstance(v, (int, float, str, bool, tuple)) and k not in ("name",)
        },
        "imagery": {
            "source": result.source,
            "baseline_passes": _passes(result.baseline),
            "target_passes": _passes(result.target),
            "failed_fetches": result.failures,
            "passes_ignored_for_transient_events": result.bad_passes,
            "attribution": "Contains modified Copernicus Sentinel data, processed by ESA. Accessed via Element84 Earth Search.",
        },
        "landcover": WORLDCOVER_CITATION,
        "candidates": {
            "total": len(result.candidates),
            "persistent": sum(c.kind == "persistent" for c in result.candidates),
            "transient": sum(c.kind == "transient" for c in result.candidates),
        },
        "outputs_sha256": {str(p.relative_to(out)): _sha256(p) for p in files},
        "disclaimer": DISCLAIMER,
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str, ensure_ascii=True) + "\n", encoding="ascii")
    return path


def write_all(result: ScanResult) -> dict[str, Path]:
    """Write every output into ``result.config.output_dir``. Returns name to path."""
    out = Path(result.config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    files += write_rasters(result, out)
    files += write_vectors(result, out)
    files += write_chips(result, out)
    files.append(write_overview(result, out))
    files.append(write_html(result, out))
    manifest = write_manifest(result, out, files)
    outputs = {str(p.relative_to(out)): p for p in files}
    outputs["manifest.json"] = manifest
    result.outputs = outputs
    return outputs


__all__ = ["DISCLAIMER", "candidate_rows", "write_all", "write_html", "write_kml", "write_manifest"]
