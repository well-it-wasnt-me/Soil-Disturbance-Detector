from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # type: ignore
from shapely.geometry import box  # type: ignore

from dgd.pipeline import DisturbanceDetector


def _write_multi_band(path: Path, bands: int = 4):
    """Create a tiny raster where band values differ in a quadrant."""
    data = np.ones((bands, 4, 4), dtype=np.float32)
    # Boost values in top-left quadrant to simulate disturbance
    data[:, 0:2, 0:2] = 5.0
    transform = from_origin(0, 4, 1, 1)
    profile = {
        "driver": "GTiff",
        "height": 4,
        "width": 4,
        "count": bands,
        "dtype": "float32",
        "transform": transform,
        "crs": "EPSG:3857",
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)
    return transform


def _write_training(path: Path, crs, disturbed_class_id: int):
    disturbed = box(0, 0, 2, 2)
    background = box(2, 2, 4, 4)
    gdf = gpd.GeoDataFrame(
        {"class_id": [disturbed_class_id, 0]},
        geometry=[disturbed, background],
        crs=crs,
    )
    gdf.to_file(path, driver="GPKG")


class _ProgressRecorder:
    def __init__(self):
        self.messages = []

    def step(self, message: str):
        self.messages.append(message)

    def close(self):
        self.messages.append("closed")


def test_pipeline_run_creates_outputs(tmp_path):
    before = tmp_path / "before.tif"
    after = tmp_path / "after.tif"
    training = tmp_path / "training.gpkg"
    outputs = tmp_path / "out"

    transform = _write_multi_band(before)
    _write_multi_band(after)  # identical size/CRS
    _write_training(training, "EPSG:3857", disturbed_class_id=3)

    args = SimpleNamespace(
        before_path=str(before),
        after_path=str(after),
        training_path=str(training),
        output_dir=str(outputs),
        disturbed_class_id=3,
        min_patch_area_m2=0.0,
        prob_threshold=0.5,
        red_band_index=2,
        nir_band_index=3,
        download_sentinel=False,
        before_date=None,
        after_date=None,
        lat=None,
        long=None,
        buffer_m=1500,
        window_days=60,
        cloud_pct=20,
        scale=10,
    )

    recorder = _ProgressRecorder()
    detector = DisturbanceDetector(args, progress=recorder)
    detector.run()

    # Check key outputs
    assert (outputs / "metrics.txt").exists()
    assert (outputs / "disturbed_binary.tif").exists()
    assert (outputs / "disturbed_polygons.gpkg").exists()
    assert (outputs / "disturbed_overlay.png").exists()
    # Progress should have been updated and closed
    assert "closed" in recorder.messages
