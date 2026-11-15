"""Score the scanner against dated waste-site growth from Global Plastic Watch.

Global Plastic Watch (Earthrise Media and the Minderoo Foundation, Kruse et
al. 2023, PLOS ONE 18(1): e0278997) published quarterly footprints of
human-verified waste sites across Southeast Asia, 2017 to mid 2021. When a
footprint grows and stays grown, we know roughly where and when new waste
showed up. That is exactly the question ``dgd scan`` answers, so it makes an
honest exam with no hand-picked answers.

Their data is CC BY-NC 4.0. This repository is CC0. So the data is never
copied into the repo: this script clones it into your cache at run time, and
everything it writes goes under ``eval/`` which git ignores. Credit where due,
and no license laundering.

Their footprints come from their own model, not from surveyors. Some growth
is their model flickering in cloudy months. The selection below only counts
growth that holds for two quarters on both sides, which filters most of that,
but treat the numbers as an estimate, not gospel.

Usage:
    python scripts/evaluate_gpw.py --cases 12 --parallel 3
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import warnings
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

GPW_REPO = "https://github.com/earthrise-media/plastics.git"
CONTOUR_DIR = "data/model_outputs/site_contours/v2.1"
CITATION = (
    "Global Plastic Watch site contours v2.1, Earthrise Media / Minderoo Foundation. Kruse et al. 2023, "
    "'Satellite monitoring of terrestrial plastic waste', PLOS ONE 18(1): e0278997. CC BY-NC 4.0."
)

log = logging.getLogger("evaluate_gpw")


def fetch_gpw(cache: Path) -> Path:
    """Sparse clone of just the contour files. About 60 MB."""
    repo = cache / "gpw-plastics"
    if not (repo / CONTOUR_DIR).exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", GPW_REPO, str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "sparse-checkout", "set", CONTOUR_DIR, "LICENSE.md"], check=True)
    return repo / CONTOUR_DIR


def load_series(contour_dir: Path):
    """Per site: sorted list of (quarter start date, area ha, geometry)."""
    import geopandas as gpd

    series = defaultdict(list)
    for path in sorted(contour_dir.glob("*.geojson")):
        gdf = gpd.read_file(path)
        country = path.name.split("_")[0]
        for _, row in gdf.iterrows():
            d = date.fromisoformat(str(row["date"])[:10])
            series[(country, row["name"])].append((d, float(row["area (km^2)"]) * 100.0, row.geometry))
    for k in series:
        series[k].sort(key=lambda r: r[0])
    return series


def select_cases(series, n: int, min_gain_ha: float = 1.0, earliest=date(2019, 9, 1), latest=date(2021, 3, 1)):
    """Sustained growth: two small quarters, then two quarters at least ``min_gain_ha`` bigger.

    Only back-to-back quarters count. A gap of a year between "small" and
    "big" says the site grew sometime that year, which is too vague to grade
    anyone on. Growth before September 2019 is skipped because its baseline
    would lean on 2017, when the archive is still thin.
    """
    from shapely.ops import unary_union

    found = []
    for (country, name), rows in series.items():
        for i in range(2, len(rows) - 1):
            d = rows[i][0]
            if not earliest <= d <= latest or (d - rows[i - 1][0]).days > 100:
                continue
            before = [r[1] for r in rows[i - 2 : i]]
            after = [r[1] for r in rows[i : i + 2]]
            if min(after) - max(before) < min_gain_ha or max(before) > 0.5 * min(after):
                continue
            # New ground = later footprint minus the earlier one. That is where the waste went.
            old = unary_union([g for g in (rows[i - 1][2], rows[i - 2][2]) if g is not None and not g.is_empty])
            new = rows[i + 1][2]
            if new is None or new.is_empty:
                continue
            grown = new.difference(old) if not old.is_empty else new
            if grown.is_empty:
                continue
            found.append({
                "country": country,
                "site": name,
                "before_quarter": rows[i - 1][0],
                "after_quarter": rows[i][0],
                "area_before_ha": round(max(before), 2),
                "area_after_ha": round(min(after), 2),
                "growth_geometry": grown,
            })
            break  # one event per site
    # Spread across countries, biggest growth first within each.
    found.sort(key=lambda c: c["area_after_ha"] - c["area_before_ha"], reverse=True)
    by_country = defaultdict(list)
    for c in found:
        by_country[c["country"]].append(c)
    picked = []
    while len(picked) < n and any(by_country.values()):
        for country in sorted(by_country):
            if by_country[country] and len(picked) < n:
                picked.append(by_country[country].pop(0))
    return picked


def run_case(case: dict, out_root: Path, cache: Path, radius_m: float, workers: int) -> dict:
    """Scan one case and check whether a lead lands on the growth."""
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform

    from dgd.aoi import grid_from_point
    from dgd.report import write_all
    from dgd.scan import ScanConfig, run_scan
    from dgd.stac import EarthSearchSource

    warnings.filterwarnings("ignore")
    geom = case["growth_geometry"]
    c = geom.centroid
    grid = grid_from_point(c.y, c.x, radius_m)
    # The quarter mosaics cover three months each. Growth happened somewhere
    # between the start of the "before" quarter and the end of the "after" one.
    start = case["before_quarter"] + timedelta(days=30)
    end = case["after_quarter"] + timedelta(days=150)
    cid = f"{case['country']}_{case['site'].rsplit('_', 1)[-1]}"
    cfg = ScanConfig(grid=grid, target_start=start, target_end=end, output_dir=out_root / cid,
                     baseline_years=2, cache_dir=cache / "imagery", name=f"GPW {cid}", max_chips=10)
    rec = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in case.items() if k != "growth_geometry"}
    rec.update(id=cid, lat=round(c.y, 5), lon=round(c.x, 5), target=[start.isoformat(), end.isoformat()])
    try:
        result = run_scan(cfg, EarthSearchSource(cache_dir=cfg.cache_dir, workers=workers))
        write_all(result)
    except Exception as exc:
        rec.update(status="error", error=str(exc))
        return rec
    to_grid = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True).transform
    truth = shp_transform(to_grid, geom).buffer(30)  # one pixel of slack for co-registration
    area_km2 = grid.width * grid.height * grid.pixel_area_m2 / 1e6
    hits = [cand for cand in result.candidates if cand.geometry.intersects(truth)]
    best = min(hits, key=lambda x: (x.kind != "persistent", x.rank)) if hits else None
    rec.update(
        status="ok",
        baseline_passes=len(result.baseline),
        target_passes=len(result.target),
        truth_area_ha=round(truth.area / 1e4, 2),
        persistent_leads=sum(x.kind == "persistent" for x in result.candidates),
        transient_leads=sum(x.kind == "transient" for x in result.candidates),
        leads_per_km2=round(len(result.candidates) / area_km2, 2),
        hit=best is not None,
        hit_id=best.id if best else None,
        hit_kind=best.kind if best else None,
        hit_rank=best.rank if best else None,
        hit_signature=best.signature if best else None,
        hit_first_seen=best.first_seen.isoformat() if best and best.first_seen else None,
    )
    return rec


def summarize(records: list[dict]) -> dict:
    ok = [r for r in records if r["status"] == "ok"]
    hits = [r for r in ok if r["hit"]]
    p_hits = [r for r in hits if r["hit_kind"] == "persistent"]

    def top(k):
        return sum(1 for r in p_hits if r["hit_rank"] <= k)

    return {
        "cases": len(records),
        "scanned": len(ok),
        "errors": len(records) - len(ok),
        "hit_any_lead": len(hits),
        "hit_persistent": len(p_hits),
        "persistent_top1": top(1),
        "persistent_top3": top(3),
        "persistent_top10": top(10),
        "median_persistent_leads_per_scan": sorted(r["persistent_leads"] for r in ok)[len(ok) // 2] if ok else None,
        "median_leads_per_km2": sorted(r["leads_per_km2"] for r in ok)[len(ok) // 2] if ok else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--cases", type=int, default=12)
    ap.add_argument("--radius-m", type=float, default=1000)
    ap.add_argument("--out", default=str(ROOT / "eval" / "gpw"))
    ap.add_argument("--cache-dir", default=str(Path.home() / ".cache" / "dgd"))
    ap.add_argument("--parallel", type=int, default=2, help="Scans at once.")
    ap.add_argument("--workers", type=int, default=8, help="Downloads per scan.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for noisy in ("rasterio", "pystac_client", "urllib3", "dgd"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cache = Path(args.cache_dir).expanduser()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    contour_dir = fetch_gpw(cache)
    cases = select_cases(load_series(contour_dir), args.cases)
    log.info("Selected %d growth events", len(cases))

    records = []
    with ProcessPoolExecutor(max_workers=args.parallel) as pool:
        futs = {pool.submit(run_case, c, out, cache, args.radius_m, args.workers): c for c in cases}
        for fut in as_completed(futs):
            rec = fut.result()
            records.append(rec)
            log.info("%s: %s", rec["id"], "HIT " + f"{rec['hit_id']}" if rec.get("hit") else rec.get("status") if rec["status"] != "ok" else "miss")
            (out / "results.json").write_text(json.dumps({"citation": CITATION, "records": records}, indent=2))

    summary = summarize(records)
    (out / "results.json").write_text(json.dumps({"citation": CITATION, "summary": summary, "records": records}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
