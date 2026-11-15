"""Build the footprints the map shows for free high resolution imagery.

National aerial photo programs cover their whole country or state with the
latest flight, so their footprint is the border. Borders come from Natural
Earth (public domain, 1:10 million), with boundary lakes cut out, clipped to
each source's coverage box (which drops Alaska, Hawaii and the overseas bits
nobody flies for this program) and simplified to roughly half a kilometre.

At that scale the coastline can be half a kilometre off, enough to drop
downtown Miami into the sea. So every footprint is grown by about a
kilometre first. On a coast that costs some water. On a land border it
claims a strip of the neighbour, where the national mosaics usually spill
over anyway. The year list in the map interface asks the service itself for
the exact box, so nothing downstream trusts these edges.

OpenAerialMap and the per-year flights are patchy and are not in this file.
The map asks OpenAerialMap for footprints in view instead.

Usage:
    python scripts/build_coverage.py            # downloads Natural Earth into the cache once
    python scripts/build_coverage.py --ne-dir DIR
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dgd.cli import default_cache_dir  # noqa: E402
from dgd.highres_sources import SOURCES  # noqa: E402

NE_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/{name}.geojson"
COUNTRIES = "ne_10m_admin_0_countries_lakes"
STATES = "ne_10m_admin_1_states_provinces_lakes"
OUT = ROOT / "src" / "dgd" / "data" / "coverage.geojson"

# source key -> (Natural Earth file, field, value)
FOOTPRINTS = {
    "naip": (COUNTRIES, "ADM0_A3", "USA"),
    "pdok": (COUNTRIES, "ADM0_A3", "NLD"),
    "ign-fr": (COUNTRIES, "ADM0_A3", "FRA"),
    "pnoa-es": (COUNTRIES, "ADM0_A3", "ESP"),
    "nrw-de": (STATES, "iso_3166_2", "DE-NW"),
    "bayern-de": (STATES, "iso_3166_2", "DE-BY"),
    "linz-nz": (COUNTRIES, "ADM0_A3", "NZL"),
}
TOLERANCE_DEG = 0.006  # about 450 to 650 m
GROW_DEG = 0.01  # about 1 km, see above
MIN_PART_DEG2 = 0.0005  # islands under about 4 km2 vanish at this tolerance anyway


def natural_earth(name: str, ne_dir: Path) -> Path:
    path = ne_dir / f"{name}.geojson"
    if not path.exists():
        ne_dir.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {name} (tens of MB, once)")
        urllib.request.urlretrieve(NE_URL.format(name=name), path)
    return path


def main() -> int:
    import geopandas as gpd
    import shapely
    from shapely.geometry import MultiPolygon, box, mapping

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ne-dir", type=Path, default=default_cache_dir() / "natural_earth")
    args = ap.parse_args()

    frames = {}
    feats = []
    for key, (layer, column, value) in FOOTPRINTS.items():
        if layer not in frames:
            frames[layer] = gpd.read_file(natural_earth(layer, args.ne_dir), columns=[column])
        df = frames[layer]
        geom = df[df[column] == value].geometry.union_all()
        geom = geom.buffer(GROW_DEG).intersection(box(*SOURCES[key].coverage))
        geom = geom.simplify(TOLERANCE_DEG, preserve_topology=True)
        geom = shapely.set_precision(geom, 1e-4)  # 4 decimals, about 10 m, and still valid
        parts = [p for p in getattr(geom, "geoms", [geom]) if p.geom_type == "Polygon" and p.area >= MIN_PART_DEG2]
        geom = MultiPolygon(parts)
        feats.append({"type": "Feature", "properties": {"source": key},
                      "geometry": {"type": "MultiPolygon", "coordinates": mapping(geom)["coordinates"]}})
        print(f"{key:10s} {len(parts):4d} parts, {sum(len(p.exterior.coords) for p in parts):6d} vertices")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection",
          "attribution": "Borders: Natural Earth (public domain), grown by about 1 km and simplified",
          "features": feats}
    OUT.write_text(json.dumps(fc, separators=(",", ":")))
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
