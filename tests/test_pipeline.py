from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")
from rasterio.transform import from_origin  # noqa: E402
from shapely.geometry import box  # noqa: E402

from dgd.pipeline import BINARY_NODATA, DisturbanceDetector  # noqa: E402


def _write_raster(path: Path, bands: int = 4, size: int = 8):
    """Tiny raster, brighter in the top-left quadrant. Rows 0-3 and cols 0-3 are y 8..4 and x 0..4."""
    data = np.ones((bands, size, size), dtype=np.float32)
    data[:, : size // 2, : size // 2] = 5.0
    data[:, -1, -1] = -9999  # one nodata pixel, to exercise the masking
    profile = {
        "driver": "GTiff",
        "height": size,
        "width": size,
        "count": bands,
        "dtype": "float32",
        "transform": from_origin(0, size, 1, 1),
        "crs": "EPSG:3857",
        "nodata": -9999,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)


def _write_training(path: Path, disturbed_class_id: int, two_per_class: bool = True):
    # Disturbed polygons sit on the bright quadrant, background on the dull one.
    disturbed = [box(0, 4, 2, 8), box(2, 4, 4, 8)] if two_per_class else [box(0, 4, 4, 8)]
    background = [box(4, 0, 6, 4), box(6, 0, 8, 3)] if two_per_class else [box(4, 0, 8, 3)]
    gdf = gpd.GeoDataFrame(
        {"class_id": [disturbed_class_id] * len(disturbed) + [0] * len(background)},
        geometry=disturbed + background,
        crs="EPSG:3857",
    )
    gdf.to_file(path, driver="GPKG")


class _ProgressRecorder:
    def __init__(self):
        self.messages = []

    def step(self, message: str):
        self.messages.append(message)

    def close(self):
        self.messages.append("closed")


def _args(tmp_path, **kw):
    base = dict(
        before_path=str(tmp_path / "before.tif"),
        after_path=str(tmp_path / "after.tif"),
        training_path=str(tmp_path / "training.gpkg"),
        output_dir=str(tmp_path / "out"),
        disturbed_class_id=3,
        min_patch_area_m2=0.0,
        prob_threshold=0.5,
        red_band_index=2,
        nir_band_index=3,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.parametrize("two_per_class", [True, False])
def test_pipeline_run_creates_outputs(tmp_path, two_per_class):
    _write_raster(tmp_path / "before.tif")
    _write_raster(tmp_path / "after.tif")
    _write_training(tmp_path / "training.gpkg", 3, two_per_class)
    recorder = _ProgressRecorder()
    DisturbanceDetector(_args(tmp_path), progress=recorder).run()

    out = tmp_path / "out"
    for name in ("metrics.txt", "disturbed_binary.tif", "disturbed_polygons.gpkg", "disturbed_overlay.png", "class_prediction.tif"):
        assert (out / name).exists(), name
    assert len([m for m in recorder.messages if m != "closed"]) == DisturbanceDetector.STEPS
    assert recorder.messages[-1] == "closed"
    metrics = (out / "metrics.txt").read_text()
    assert ("held-out polygons" in metrics) == two_per_class
    with rasterio.open(out / "disturbed_binary.tif") as src:
        binary = src.read(1)
        assert src.nodata == BINARY_NODATA
    assert binary[0, 0] == 1 and binary[6, 6] == 0 and binary[-1, -1] == BINARY_NODATA


def test_pipeline_rejects_bad_band_index(tmp_path):
    _write_raster(tmp_path / "before.tif")
    _write_raster(tmp_path / "after.tif")
    _write_training(tmp_path / "training.gpkg", 3)
    with pytest.raises(ValueError, match="outside the raster"):
        DisturbanceDetector(_args(tmp_path, nir_band_index=9)).run()


def test_pipeline_rejects_mismatched_rasters(tmp_path):
    _write_raster(tmp_path / "before.tif", size=8)
    _write_raster(tmp_path / "after.tif", size=6)
    _write_training(tmp_path / "training.gpkg", 3)
    with pytest.raises(ValueError, match="same shape"):
        DisturbanceDetector(_args(tmp_path)).run()


def test_pipeline_requires_class_id(tmp_path):
    _write_raster(tmp_path / "before.tif")
    _write_raster(tmp_path / "after.tif")
    gpd.GeoDataFrame({"label": [1]}, geometry=[box(0, 4, 4, 8)], crs="EPSG:3857").to_file(tmp_path / "training.gpkg", driver="GPKG")
    with pytest.raises(ValueError, match="class_id"):
        DisturbanceDetector(_args(tmp_path)).run()


def test_polygon_split_covers_every_class_even_when_unbalanced():
    cls = np.array([0] * 10 + [1] * 2 + [2] * 2)  # 14 polygons, two classes with only two each
    groups = np.repeat(np.arange(len(cls)), 5)
    y = np.repeat(cls, 5)
    x = np.arange(len(y), dtype=float)[:, None]
    x_tr, x_va, y_tr, y_va, note = DisturbanceDetector._split(None, x, y, groups)
    assert "held-out polygons" in note
    assert set(y_tr) == set(y_va) == {0, 1, 2}
    tr_groups, va_groups = set(groups[x_tr[:, 0].astype(int)]), set(groups[x_va[:, 0].astype(int)])
    assert not tr_groups & va_groups and len(va_groups) == 4


def test_split_warning_names_the_class_that_is_short(caplog):
    groups = np.repeat(np.arange(3), 10)
    y = np.repeat([0, 0, 1], 10)
    x = np.arange(len(y), dtype=float)[:, None]
    *_, note = DisturbanceDetector._split(None, x, y, groups)
    assert "random pixels" in note and "Class 1 has fewer than two polygons" in caplog.text
