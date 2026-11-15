"""Blind test of ``dgd graves`` with synthetic graves planted in real aerial imagery.

What it does:

1. Fetches 8 cm aerial photos (red, green, blue, near infrared) of three
   200 x 200 m Dutch landscapes for two years from PDOK: a cattle pasture
   with ditches, a crop field against a woodland edge, and a plowed field.
2. Plants 15 synthetic graves per landscape in the newer image, in three
   states: fresh soil, regrowing grass, and sod put back by somebody careful
   (pasture only, there is no sod on a plowed field). See
   ``synthetic_graves.plant_graves``.
3. Runs the detector without telling it anything, in single-image and
   before/after mode, with and without the NIR band, at 10, 25 and 60 cm.
4. Counts, for each grave found, how many false alarms rank above it. That is
   the number of wrong spots a search team visits first.

It measures what the published drone studies could not: false alarms in
real, cluttered ground. It does not prove the detector finds real graves.
Real graves are covered with leaves and branches by people who do not want
them found. Treat these numbers as an upper bound.

Usage:
    python scripts/evaluate_graves_synthetic.py --out eval/graves_synthetic
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from dgd.graves import find_graves, gather_evidence  # noqa: E402
from dgd.highres import coregister, grid_for_raster, match_histograms, read_to_grid  # noqa: E402
from dgd.highres_sources import fetch_pdok  # noqa: E402
from synthetic_graves import plant_graves, score_detection  # noqa: E402

SITES = {"pasture": (52.1105, 4.8790), "crop_edge": (52.0440, 5.7330), "plowed": (52.5050, 5.6000)}
YEARS = (2025, 2026)
MODES = ("single_rgbn", "single_rgb", "change_rgbn")


def bbox_200m(lat: float, lon: float):
    import math

    dlat = 100 / 111320
    dlon = 100 / (111320 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def degrade(fine, grid):
    """Average fine imagery down to ``grid`` like a coarser sensor would."""
    from scipy.ndimage import gaussian_filter, zoom

    k = fine.grid.res / grid.res
    data = np.stack([zoom(gaussian_filter(np.nan_to_num(b), 0.5 / k), k, order=1) for b in fine.data])
    out = np.full((data.shape[0], grid.height, grid.width), np.nan, dtype=np.float32)
    hh, ww = min(grid.height, data.shape[1]), min(grid.width, data.shape[2])
    out[:, :hh, :ww] = data[:, :hh, :ww]
    return out


def run(out: Path, data_dir: Path, resolutions, n_graves: int = 15, seed: int = 7) -> list[dict]:
    rows = []
    for site, (lat, lon) in SITES.items():
        paths = {y: data_dir / f"{site}_{y}.tif" for y in YEARS}
        for y, p in paths.items():
            if not p.exists():
                fetch_pdok(bbox_200m(lat, lon), y, p, res=0.08)
        scenarios = ("fresh", "regrowing", "turf") if site == "pasture" else ("fresh", "regrowing")
        for res in resolutions:
            grid = grid_for_raster(str(paths[YEARS[1]]), res)
            after0 = read_to_grid(str(paths[YEARS[1]]), grid)
            before0 = match_histograms(coregister(read_to_grid(str(paths[YEARS[0]]), grid), after0), after0)
            for scenario in scenarios:
                # Always plant at native 8 cm, then average down, so coarse runs see a realistic smudge.
                fine = read_to_grid(str(paths[YEARS[1]]), grid_for_raster(str(paths[YEARS[1]]), 0.08))
                graves = plant_graves(fine, n_graves, np.random.default_rng(seed), scenario=scenario)
                after = copy.deepcopy(after0)
                after.data = degrade(fine, grid) if res > 0.09 else fine.data
                for mode in MODES:
                    a = copy.deepcopy(after)
                    b = copy.deepcopy(before0) if mode.startswith("change") else None
                    if mode.endswith("_rgb"):
                        for img in (a, b):
                            if img is not None:
                                img.data = img.data[:3]
                                img.roles = {k: v for k, v in img.roles.items() if k != "nir"}
                    mask = np.isfinite(a.data).all(axis=0)
                    if b is not None:
                        mask &= np.isfinite(b.data).all(axis=0)
                    ev = gather_evidence(a, b, None, mask)
                    _, cands = find_graves(ev, grid, 3.0)
                    r = score_detection(cands, graves, grid.res)
                    ha = mask.sum() * grid.res ** 2 / 1e4
                    rec = {
                        "site": site, "res_m": round(grid.res, 2), "scenario": scenario, "mode": mode,
                        "graves": r["graves"], "found": r["found"],
                        "within_5_false": sum(1 for x in r["false_ahead"] if x <= 5),
                        "within_20_false": sum(1 for x in r["false_ahead"] if x <= 20),
                        "false_per_ha": round(r["false_alarms"] / ha, 1),
                        "false_ahead": r["false_ahead"],
                    }
                    rows.append(rec)
                    logging.info("%-9s %.2f m %-9s %-12s found %2d/%d  <=5 FA %2d  <=20 FA %2d  FA/ha %6.1f",
                                 site, rec["res_m"], scenario, mode, rec["found"], rec["graves"],
                                 rec["within_5_false"], rec["within_20_false"], rec["false_per_ha"])
                    (out / "results.json").write_text(json.dumps(rows, indent=1))
    return rows


def table(rows: list[dict]) -> str:
    """Markdown summary: graves found with at most 5 false alarms ahead, out of 15."""
    lines = ["| Ground | Grave | Mode | " + " | ".join(f"{r * 100:.0f} cm" for r in sorted({x['res_m'] for x in rows})) + " |"]
    lines.append("|" + " --- |" * (3 + len({x["res_m"] for x in rows})))
    keys = []
    for r in rows:
        k = (r["site"], r["scenario"], r["mode"])
        if k not in keys:
            keys.append(k)
    for site, scen, mode in keys:
        cells = []
        for res in sorted({x["res_m"] for x in rows}):
            m = [x for x in rows if (x["site"], x["scenario"], x["mode"], x["res_m"]) == (site, scen, mode, res)]
            cells.append(f"{m[0]['within_5_false']}/{m[0]['graves']}" if m else "")
        lines.append(f"| {site} | {scen} | {mode} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "eval" / "graves_synthetic"))
    ap.add_argument("--data-dir", help="Where to keep the PDOK imagery. Default: <out>/imagery")
    ap.add_argument("--res", default="0.1,0.25,0.6", help="Working resolutions, metres, comma separated.")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for noisy in ("rasterio", "dgd"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    data_dir = Path(args.data_dir) if args.data_dir else out / "imagery"
    data_dir.mkdir(parents=True, exist_ok=True)
    rows = run(out, data_dir, [float(x) for x in args.res.split(",")])
    md = table(rows)
    (out / "summary.md").write_text(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
