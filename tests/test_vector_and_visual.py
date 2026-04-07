from pathlib import Path

import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # type: ignore
from shapely.geometry import box  # type: ignore

from dgd.vectorization import raster_to_polygons
from dgd.visualization import save_overlay


def test_raster_to_polygons_creates_features():
    mask = np.array([[1, 0], [0, 0]], dtype=np.uint8)
    transform = from_origin(0, 2, 1, 1)
    gdf = raster_to_polygons(mask, transform, "EPSG:3857", min_patch_area_m2=0)
    assert len(gdf) == 1
    assert gdf.iloc[0].area_m2 > 0


def test_save_overlay_writes_png(tmp_path):
    arr = np.ones((3, 3, 3), dtype=np.float32)
    path = tmp_path / "before.tif"
    transform = from_origin(0, 3, 1, 1)
    profile = {
        "driver": "GTiff",
        "height": 3,
        "width": 3,
        "count": 3,
        "dtype": "float32",
        "transform": transform,
        "crs": "EPSG:3857",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)

    geom = box(0, 0, 2, 2)
    gdf = gpd.GeoDataFrame({"value": [1]}, geometry=[geom], crs="EPSG:3857")
    out_png = tmp_path / "overlay.png"
    save_overlay(str(path), gdf, out_png, red_band_index=0)
    assert out_png.exists()
