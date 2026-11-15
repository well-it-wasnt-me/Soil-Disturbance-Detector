"""Reports for grave searches.

Same idea as the dump reports, different priorities. The reader here is a
search team, possibly standing in a field with a phone. So: GPX waypoints
for walking to each candidate, close-up clips at a scale where a grave is
visible, and the rule that matters most printed where nobody can miss it.
"""

from __future__ import annotations

import csv
import html
import json
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np

from dgd import __version__
from dgd.grave_search import GraveSearchResult
from dgd.report import _ascii, _git_commit, _outline, _sha256, _shared_stretch, _stretch, plt

DISCLAIMER = (
    "These are candidates for a search, not findings. Each marks a grave-sized patch of ground that "
    "looks disturbed compared with its surroundings or with an earlier image. Dung patches, molehills, "
    "animal scrapes, dumped soil, vehicle ruts and plain bad luck look the same from above. The best "
    "published drone studies find roughly half of simulated graves. A clean result does not clear an area."
)

SCENE_RULES = (
    "Do not dig. If a candidate looks like a grave on the ground, treat it as a possible crime scene: "
    "stop, mark it, keep people and dogs off it, photograph it, record the position, and call the "
    "forensic team. Evidence destroyed by an enthusiastic shovel does not come back."
)


def rows(result: GraveSearchResult) -> list[dict]:
    out = []
    for c in result.candidates:
        out.append({
            "id": c.id,
            "rank": c.rank,
            "lat": round(c.lat, 7),
            "lon": round(c.lon, 7),
            "length_m": round(c.length_m, 2),
            "width_m": round(c.width_m, 2),
            "area_m2": round(c.area_m2, 2),
            "orientation_deg": round(c.orientation_deg),
            "evidence": round(c.evidence_mean, 2),
            "evidence_max": round(c.evidence_max, 2),
            "shape_fit": round(c.shape_fit, 2),
            "dig_direction": round(c.dig_mean, 2),
            **{f"layer_{k}": round(v, 2) for k, v in c.layers.items()},
            "access_m": None if c.access_m is None or not np.isfinite(c.access_m) else round(c.access_m),
            "rank_score": round(c.rank_score, 2),
            "maps_url": c.maps_url,
        })
    return out


def write_vectors(result: GraveSearchResult, out: Path) -> list[Path]:
    import geopandas as gpd

    rs = rows(result)
    paths = []
    gj = out / "candidates.geojson"
    if rs:
        gdf = gpd.GeoDataFrame(rs, geometry=[c.geometry for c in result.candidates], crs=result.after.grid.crs).to_crs("EPSG:4326")
        gdf.to_file(gj, driver="GeoJSON")
    else:
        gj.write_text('{"type": "FeatureCollection", "features": []}\n', encoding="ascii")
    paths.append(gj)
    csv_path = out / "candidates.csv"
    with csv_path.open("w", newline="", encoding="ascii", errors="replace") as f:
        w = csv.DictWriter(f, fieldnames=list(rs[0].keys()) if rs else ["id"])
        w.writeheader()
        w.writerows(rs)
    paths.append(csv_path)
    paths.append(write_gpx(result, out / "candidates.gpx"))
    paths.append(write_kml(result, out / "candidates.kml"))
    return paths


def write_gpx(result: GraveSearchResult, path: Path) -> Path:
    """Waypoints for handheld GPS units and phone apps. Search teams walk, they do not open QGIS."""
    parts = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<gpx version="1.1" creator="Soil-Disturbance-Detector" xmlns="http://www.topografix.com/GPX/1/1">']
    for c in result.candidates:
        desc = f"rank {c.rank}, {c.length_m:.1f} x {c.width_m:.1f} m, evidence {c.evidence_mean:.1f}"
        parts.append(f'<wpt lat="{c.lat:.7f}" lon="{c.lon:.7f}"><name>{c.id}</name><desc>{escape(desc)}</desc><sym>Flag, Red</sym></wpt>')
    parts.append("</gpx>")
    path.write_text("\n".join(parts) + "\n", encoding="ascii")
    return path


def write_kml(result: GraveSearchResult, path: Path) -> Path:
    import shapely
    from pyproj import Transformer

    tr = Transformer.from_crs(result.after.grid.crs, "EPSG:4326", always_xy=True)
    name = escape(_ascii(result.config.name or "Grave search"))
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>', f"<name>{name}</name>",
             '<Style id="c"><LineStyle><color>ff0000ff</color><width>2</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>']
    for c, r in zip(result.candidates, rows(result)):
        g = shapely.transform(c.geometry, lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])))
        desc = "<br/>".join(f"{escape(k)}: {escape(str(v))}" for k, v in r.items() if k != "maps_url")
        parts.append(f"<Placemark><name>{c.id}</name><styleUrl>#c</styleUrl><description><![CDATA[{_ascii(desc)}]]></description><MultiGeometry>")
        parts.append(f"<Point><coordinates>{c.lon:.7f},{c.lat:.7f},0</coordinates></Point>")
        for poly in getattr(g, "geoms", [g]):
            coords = " ".join(f"{x:.8f},{y:.8f},0" for x, y in poly.exterior.coords)
            parts.append(f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon>")
        parts.append("</MultiGeometry></Placemark>")
    parts.append("</Document></kml>")
    path.write_text("\n".join(parts) + "\n", encoding="ascii")
    return path


def _rgb(img, r0, r1, c0, c1):
    """True color, or color infrared (NIR as red) for cameras without a blue band."""
    return img.data[img.display_bands(), r0:r1, c0:c1]


def write_chips(result: GraveSearchResult, out: Path) -> list[Path]:
    grid = result.after.grid
    res = grid.res
    chips = out / "chips"
    chips.mkdir(exist_ok=True)
    half = int(round(6.0 / res))  # 12 m across: a grave is obvious at this scale or not at all
    left, top = grid.transform.c, grid.transform.f
    paths = []
    for c in result.candidates[: result.config.max_chips]:
        rc = (c.rows.start + c.rows.stop) // 2
        cc = (c.cols.start + c.cols.stop) // 2
        r0, r1 = max(0, rc - half), min(grid.height, rc + half)
        c0, c1 = max(0, cc - half), min(grid.width, cc + half)
        extent = [left + c0 * res, left + c1 * res, top - r1 * res, top - r0 * res]
        after = _rgb(result.after, r0, r1, c0, c1)
        panels = []
        if result.before is not None:
            before = _rgb(result.before, r0, r1, c0, c1)
            lo, hi = _shared_stretch(before, after)
            panels.append((_stretch(before, lo, hi), f"Before{' (' + result.before.date + ')' if result.before.date else ''}"))
        else:
            lo, hi = _shared_stretch(after)
        panels.append((_stretch(after, lo, hi), f"After{' (' + result.after.date + ')' if result.after.date else ''}"))
        fig, axes = plt.subplots(1, len(panels) + 1, figsize=(4.2 * (len(panels) + 1), 4.6))
        for ax, (img, title) in zip(axes, panels):
            ax.imshow(img, extent=extent)
            _outline(ax, c.geometry, "red", 1.2)
            ax.set_title(title, fontsize=9)
            ax.set_xticks([])
            ax.set_yticks([])
        ax = axes[-1]
        ev = result.evidence.combined[r0:r1, c0:c1]
        im = ax.imshow(ev, extent=extent, cmap="magma", vmin=0, vmax=max(8.0, float(np.nanmax(ev)) if np.isfinite(ev).any() else 8.0))
        _outline(ax, c.geometry, "cyan", 1.0)
        ax.set_title("Evidence (robust z)", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        acc = "" if c.access_m is None or not np.isfinite(c.access_m) else f" | {c.access_m:.0f} m to mapped track"
        fig.suptitle(_ascii(f"{c.id} | {c.lat:.6f}, {c.lon:.6f} | {c.length_m:.1f} x {c.width_m:.1f} m | evidence {c.evidence_mean:.1f} | "
                            f"shape {c.shape_fit:.2f}{acc} | 12 m across"), fontsize=9)
        fig.tight_layout()
        path = chips / f"{c.id}.png"
        fig.savefig(path, dpi=110)
        plt.close(fig)
        paths.append(path)
    return paths


def write_overview(result: GraveSearchResult, out: Path, max_labels: int = 50) -> Path:
    grid = result.after.grid
    left, bottom, right, top = grid.bounds
    step = max(1, int(max(grid.width, grid.height) / 2500))
    img = _rgb(result.after, 0, grid.height, 0, grid.width)[:, ::step, ::step]
    lo, hi = _shared_stretch(img)
    fig, ax = plt.subplots(figsize=(10, 10 * grid.height / max(grid.width, 1) + 0.6))
    ax.imshow(_stretch(img, lo, hi), extent=[left, right, bottom, top])
    for c in result.candidates[:max_labels]:
        _outline(ax, c.geometry, "red", 1.0)
        ax.annotate(c.id, (c.geometry.centroid.x, c.geometry.centroid.y), color="white", fontsize=6, xytext=(3, 3),
                    textcoords="offset points", bbox=dict(facecolor="black", alpha=0.6, edgecolor="none", pad=1))
    ax.set_title(_ascii(f"{result.config.name or 'Grave search'}: top {min(len(result.candidates), max_labels)} of "
                        f"{len(result.candidates)} candidates"), fontsize=10)
    ax.tick_params(labelsize=7)
    fig.tight_layout()
    path = out / "overview.png"
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def write_html(result: GraveSearchResult, out: Path) -> Path:
    cfg = result.config
    e = lambda x: html.escape(_ascii(str(x)))  # noqa: E731
    g = result.after.grid
    area_ha = result.mask.sum() * g.res * g.res / 1e4
    layers = ", ".join(result.evidence.layers)
    body = [
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>{e(cfg.name or 'Grave search')}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:16px;max-width:1300px;color:#222;background:#fff}"
        "table{border-collapse:collapse;font-size:13px;width:100%}th,td{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left}"
        "th{background:#f3f3f3}.rule{background:#fde8e8;border-left:4px solid #c0392b;padding:8px 12px;margin:12px 0}"
        ".note{background:#fff7e6;border-left:4px solid #f0a020;padding:8px 12px;margin:12px 0}img{max-width:100%}</style></head><body>",
        f"<h1>{e(cfg.name or 'Grave search')}</h1>",
        f"<div class='rule'><b>{e(SCENE_RULES)}</b></div>",
        f"<div class='note'>{e(DISCLAIMER)}</div>",
        f"<p>Searched {area_ha:.2f} ha at {g.res * 100:.0f} cm per pixel using: {e(layers)}"
        f"{' (single image, no before)' if result.before is None else ''}. {len(result.candidates)} candidates.</p>",
    ]
    if result.notes:
        body.append("<p>Notes: " + ". ".join(e(n) for n in result.notes) + ".</p>")
    body += ["<p><img src='overview.png' alt='overview'></p>",
             "<table><tr><th>ID</th><th>Where</th><th>Size m</th><th>Evidence</th><th>Shape</th><th>To track</th></tr>"]
    for r in rows(result):
        acc = "" if r["access_m"] is None else f"{r['access_m']} m"
        body.append(f"<tr><td><a href='#{e(r['id'])}'>{e(r['id'])}</a></td><td><a href='{e(r['maps_url'])}'>{r['lat']:.6f}, {r['lon']:.6f}</a></td>"
                    f"<td>{r['length_m']} x {r['width_m']}</td><td>{r['evidence']}</td><td>{r['shape_fit']}</td><td>{e(acc)}</td></tr>")
    body.append("</table>")
    for c in result.candidates[: cfg.max_chips]:
        body.append(f"<h3 id='{e(c.id)}'>{e(c.id)}</h3><img src='chips/{e(c.id)}.png' alt='{e(c.id)}' loading='lazy'>")
    body.append("<h2>Files</h2><ul><li><code>candidates.gpx</code>: waypoints for a handheld GPS or phone</li>"
                "<li><code>candidates.kml</code>, <code>candidates.geojson</code>, <code>candidates.csv</code></li>"
                "<li><code>evidence.tif</code>: the evidence map</li><li><code>manifest.json</code>: inputs, settings, file hashes</li></ul>")
    body.append(f"<p style='color:#777;font-size:12px'>Soil-Disturbance-Detector {e(__version__)}. Sources: {e('; '.join(cfg.sources) or 'user supplied imagery')}.</p></body></html>")
    path = out / "report.html"
    path.write_text("\n".join(body) + "\n", encoding="ascii")
    return path


def write_evidence(result: GraveSearchResult, out: Path) -> Path:
    import rasterio

    g = result.after.grid
    path = out / "evidence.tif"
    with rasterio.open(path, "w", driver="GTiff", height=g.height, width=g.width, count=1, dtype="float32", crs=g.crs,
                       transform=g.transform, nodata=float("nan"), compress="deflate", tiled=g.width >= 256 and g.height >= 256) as dst:
        dst.write(result.evidence.combined.astype(np.float32), 1)
        dst.set_band_description(1, "Combined grave evidence, robust z")
    return path


def write_manifest(result: GraveSearchResult, out: Path, files: list[Path]) -> Path:
    cfg = result.config

    def file_hash(p):
        try:
            return _sha256(Path(p))
        except Exception:
            return None

    manifest = {
        "tool": "Soil-Disturbance-Detector (dgd graves)",
        "version": __version__,
        "git_commit": _git_commit(),
        "command": [_ascii(a) for a in sys.argv],
        "started_utc": result.started.isoformat(),
        "finished_utc": result.finished.isoformat() if result.finished else None,
        "inputs": {
            "after": {"path": cfg.after_path, "sha256": file_hash(cfg.after_path), "date": cfg.after_date},
            "before": {"path": cfg.before_path, "sha256": file_hash(cfg.before_path), "date": cfg.before_date} if cfg.before_path else None,
            "dsm": {"path": cfg.dsm_path, "sha256": file_hash(cfg.dsm_path)} if cfg.dsm_path else None,
            "aoi": cfg.aoi_path,
            "sources": cfg.sources,
        },
        "grid": {"crs": result.after.grid.crs, "transform": list(result.after.grid.transform)[:6],
                 "width": result.after.grid.width, "height": result.after.grid.height},
        "parameters": {k: v for k, v in vars(cfg).items() if isinstance(v, (int, float, str, bool)) or v is None},
        "evidence_layers": list(result.evidence.layers),
        "notes": result.notes,
        "candidates": len(result.candidates),
        "outputs_sha256": {str(p.relative_to(out)): _sha256(p) for p in files},
        "disclaimer": DISCLAIMER,
        "scene_rules": SCENE_RULES,
    }
    path = out / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, default=str, ensure_ascii=True) + "\n", encoding="ascii")
    return path


def write_map_layers(result: GraveSearchResult, out: Path) -> list[Path]:
    """After, before and evidence as map overlays for the UI's swipe view."""
    from dgd.weblayers import safe_write_web_layers

    cfg = result.config
    after = result.after
    images = {"after": (after.data[after.display_bands()], f"Searched image{', ' + cfg.after_date if cfg.after_date else ''}")}
    if result.before is not None:
        b = result.before
        images = {"before": (b.data[b.display_bands()], f"Earlier image{', ' + cfg.before_date if cfg.before_date else ''}"), **images}
    heat = {"score": (result.evidence.combined, cfg.min_score, "Grave evidence")}
    return safe_write_web_layers(out, after.grid, images, heat)


def write_all(result: GraveSearchResult) -> dict[str, Path]:
    out = Path(result.config.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = [write_evidence(result, out)]
    files += write_vectors(result, out)
    files += write_chips(result, out)
    files.append(write_overview(result, out))
    files.append(write_html(result, out))
    files += write_map_layers(result, out)
    manifest = write_manifest(result, out, files)
    outputs = {str(p.relative_to(out)): p for p in files}
    outputs["manifest.json"] = manifest
    result.outputs = outputs
    return outputs


__all__ = ["DISCLAIMER", "SCENE_RULES", "rows", "write_all", "write_gpx", "write_map_layers"]
