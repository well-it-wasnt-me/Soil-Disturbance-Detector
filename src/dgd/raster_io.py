"""Raster IO helpers for reading, masking nodata, and saving outputs."""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import numpy as np
import rasterio


RasterRead = Tuple[np.ndarray, dict, rasterio.Affine, rasterio.crs.CRS, float | int | None]


def read_raster(path: str) -> RasterRead:
    """Load a raster and return data plus geospatial metadata.

    Returns a tuple of ``(array, profile, transform, crs, nodata)`` with
    ``array`` cast to ``float32`` for downstream numeric consistency.
    """

    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        profile = src.profile.copy()
        transform = src.transform
        crs = src.crs
        nodata = src.nodata
    return arr, profile, transform, crs, nodata


def apply_nodata(arr: np.ndarray, nodata_value) -> np.ndarray:
    """Replace nodata tokens with ``np.nan`` while preserving dtype."""

    arr = arr.astype(np.float32, copy=True)
    if nodata_value is None or (isinstance(nodata_value, float) and np.isnan(nodata_value)):
        return arr
    arr[arr == nodata_value] = np.nan
    return arr


def save_raster(path: Path, array: np.ndarray, profile: dict, dtype: str, nodata):
    """Write a single-band raster to disk with LZW compression."""

    out_profile = profile.copy()
    out_profile.update(count=1, dtype=dtype, compress="lzw", nodata=nodata)
    with rasterio.open(path, "w", **out_profile) as dst:
        dst.write(array.astype(dtype), 1)


__all__ = ["apply_nodata", "read_raster", "save_raster"]
