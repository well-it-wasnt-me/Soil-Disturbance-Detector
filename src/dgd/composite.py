"""Median composites from the free Sentinel-2 archive, written as GeoTIFFs."""

from __future__ import annotations

import logging
import warnings
from datetime import date
from pathlib import Path
from typing import Mapping

import numpy as np

from dgd.aoi import Grid
from dgd.stac import REFLECTANCE_BANDS

log = logging.getLogger(__name__)

BAND_LABELS = ("B02 blue", "B03 green", "B04 red", "B08 nir", "B11 swir16", "B12 swir22")


def median_composite(source, grid: Grid, start: date, end: date) -> np.ndarray:
    """Cloud-masked per-pixel median reflectance, shaped (6, rows, cols)."""
    acqs = source.search(grid, start, end)
    if not acqs:
        raise RuntimeError(f"No Sentinel-2 passes between {start} and {end}. Widen the window.")
    arrays = source.fetch_all(acqs, grid)
    if not arrays:
        raise RuntimeError(f"Every pass between {start} and {end} was clouded out. Widen the window.")
    stack = np.stack([np.asarray(a, dtype=np.float32) for a in arrays.values()])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        comp = np.nanmedian(stack, axis=0).astype(np.float32)
    log.info("Composite %s..%s from %d passes, %.0f%% of pixels filled", start, end, len(arrays), 100 * np.isfinite(comp[0]).mean())
    return comp


def write_composite(path: Path, arr: np.ndarray, grid: Grid) -> Path:
    import rasterio

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": grid.height,
        "width": grid.width,
        "count": arr.shape[0],
        "dtype": "float32",
        "crs": grid.crs,
        "transform": grid.transform,
        "nodata": float("nan"),
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr.astype(np.float32))
        for i, label in enumerate(BAND_LABELS, start=1):
            dst.set_band_description(i, label)
    return path


def download_composites(grid: Grid, windows: Mapping[str, tuple[date, date]], paths: Mapping[str, Path], source) -> dict[str, Path]:
    """Write one composite per named window. Band order: blue, green, red, nir, swir16, swir22."""
    assert len(REFLECTANCE_BANDS) == len(BAND_LABELS)
    return {name: write_composite(paths[name], median_composite(source, grid, *windows[name]), grid) for name in windows}


__all__ = ["BAND_LABELS", "download_composites", "median_composite", "write_composite"]
