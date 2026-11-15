"""ESA WorldCover 2021 land cover, read straight from its public bucket.

Used for two things: dropping pixels where a dump makes no sense (open water,
glaciers) and telling the analyst what kind of land each lead sits on, so
they can decide for themselves how suspicious "bare ground on cropland" is.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from dgd.aoi import Grid

log = logging.getLogger(__name__)

WORLDCOVER_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
WORLDCOVER_CITATION = "ESA WorldCover 10 m 2021 v200, Zanaga et al. 2022, doi:10.5281/zenodo.7254221, CC BY 4.0"

CLASS_NAMES = {
    0: "unknown",
    10: "tree cover",
    20: "shrubland",
    30: "grassland",
    40: "cropland",
    50: "built-up",
    60: "bare or sparse vegetation",
    70: "snow and ice",
    80: "permanent water",
    90: "herbaceous wetland",
    95: "mangroves",
    100: "moss and lichen",
}

# Short names accepted on the command line.
NAME_TO_CODE = {
    "tree": 10,
    "shrub": 20,
    "grass": 30,
    "cropland": 40,
    "built-up": 50,
    "bare": 60,
    "snow": 70,
    "water": 80,
    "wetland": 90,
    "mangrove": 95,
    "moss": 100,
}

DEFAULT_EXCLUDE = (70, 80)


def parse_classes(text: str | None) -> tuple[int, ...]:
    """Turn 'water,snow,built-up' into WorldCover codes."""
    if not text:
        return ()
    codes = []
    for raw in text.split(","):
        name = raw.strip().lower()
        if not name:
            continue
        if name.isdigit() and int(name) in CLASS_NAMES:
            codes.append(int(name))
        elif name in NAME_TO_CODE:
            codes.append(NAME_TO_CODE[name])
        else:
            raise ValueError(f"Unknown land cover class '{raw}'. Pick from: {', '.join(NAME_TO_CODE)}")
    return tuple(sorted(set(codes)))


def tile_names(bbox: tuple[float, float, float, float]) -> list[str]:
    """WorldCover 3x3 degree tiles touching a lon/lat bbox."""
    min_lon, min_lat, max_lon, max_lat = bbox
    names = []
    for lat0 in range(int(math.floor(min_lat / 3) * 3), int(math.floor(max_lat / 3) * 3) + 1, 3):
        for lon0 in range(int(math.floor(min_lon / 3) * 3), int(math.floor(max_lon / 3) * 3) + 1, 3):
            ns = "N" if lat0 >= 0 else "S"
            ew = "E" if lon0 >= 0 else "W"
            names.append(f"{ns}{abs(lat0):02d}{ew}{abs(lon0):03d}")
    return names


def load_worldcover(grid: Grid, cache_dir: Path | None = None) -> np.ndarray:
    """WorldCover classes on the grid as uint8. Zeros where unavailable.

    A missing land cover layer is an inconvenience, not a reason to abandon
    the scan. Failures get logged and the scan carries on without it.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    from dgd.stac import GDAL_ENV

    cache = Path(cache_dir) / grid.key() / "worldcover.npy" if cache_dir else None
    if cache is not None and cache.exists():
        return np.load(cache)

    out = np.zeros(grid.shape, dtype=np.uint8)
    with rasterio.Env(**GDAL_ENV):
        for name in tile_names(grid.lonlat_bbox()):
            url = WORLDCOVER_URL.format(tile=name)
            try:
                with rasterio.open(url) as src, WarpedVRT(
                    src,
                    crs=grid.crs,
                    transform=grid.transform,
                    width=grid.width,
                    height=grid.height,
                    resampling=Resampling.nearest,
                    src_nodata=0,
                    nodata=0,
                ) as vrt:
                    arr = vrt.read(1)
            except Exception as exc:  # ocean tiles do not exist, networks fail
                log.info("WorldCover tile %s unavailable: %s", name, exc)
                continue
            fill = (out == 0) & (arr > 0)
            out[fill] = arr[fill]
    if not out.any():
        log.warning("No WorldCover data for this area. Land cover filters are off.")
    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.save(cache, out)
    return out


def majority_class(codes: np.ndarray) -> int:
    """Most common nonzero class in an array, or 0."""
    vals = codes[codes > 0]
    if vals.size == 0:
        return 0
    uniq, counts = np.unique(vals, return_counts=True)
    return int(uniq[np.argmax(counts)])


__all__ = [
    "CLASS_NAMES",
    "DEFAULT_EXCLUDE",
    "NAME_TO_CODE",
    "WORLDCOVER_CITATION",
    "load_worldcover",
    "majority_class",
    "parse_classes",
    "tile_names",
]
