from pathlib import Path

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # type: ignore

from dgd.raster_io import apply_nodata, read_raster, save_raster


def _write_temp_raster(tmp_path: Path, name: str, arr: np.ndarray, nodata=None):
    path = tmp_path / name
    transform = from_origin(0, arr.shape[1], 1, 1)
    profile = {
        "driver": "GTiff",
        "height": arr.shape[1],
        "width": arr.shape[2],
        "count": arr.shape[0],
        "dtype": "float32",
        "transform": transform,
        "crs": "EPSG:3857",
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
    return path


def test_read_and_save_raster_roundtrip(tmp_path):
    arr = np.ones((1, 3, 3), dtype=np.float32)
    path = _write_temp_raster(tmp_path, "in.tif", arr, nodata=-99)

    data, profile, transform, crs, nodata = read_raster(str(path))
    assert nodata == -99
    assert data.shape == (1, 3, 3)

    out = tmp_path / "out.tif"
    save_raster(out, data[0], profile, "float32", nodata=-99)
    with rasterio.open(out) as src:
        saved = src.read(1)
        assert np.allclose(saved, arr[0])


def test_apply_nodata_sets_nan():
    arr = np.array([[1, -99], [2, -99]], dtype=np.float32)
    out = apply_nodata(arr, -99)
    assert np.isnan(out[0, 1])
    assert np.isnan(out[1, 1])
