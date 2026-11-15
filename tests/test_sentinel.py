"""Earth Engine backend, tested against a fake ``ee`` because nobody wants CI holding a Google login."""

import sys
import types
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine

from dgd.aoi import grid_from_point
from dgd.sentinel import download_sentinel_pair_ee


class _Chain:
    """Pretends to be every Earth Engine object. Every method returns itself."""

    def __init__(self, size=1):
        self._size = size

    def __getattr__(self, name):
        return lambda *a, **k: self

    def size(self):
        return types.SimpleNamespace(getInfo=lambda: self._size)


def _install_fakes(monkeypatch, size=1):
    calls = {}
    fake_ee = types.SimpleNamespace(
        Geometry=types.SimpleNamespace(Rectangle=lambda coords: ("rect", tuple(coords))),
        Filter=types.SimpleNamespace(lt=lambda name, value: ("lt", name, value)),
        ImageCollection=lambda name: _Chain(size),
        Initialize=lambda project=None: calls.setdefault("project", project),
        Authenticate=lambda: None,
    )

    def ee_export_image(image, filename, scale, region, crs, file_per_band):
        calls.setdefault("exports", []).append((filename, scale, crs))
        if calls.get("fail_export"):
            return  # geemap prints the error and returns
        # What Earth Engine hands back: float reflectance, masked pixels filled.
        arr = np.full((6, 4, 4), 0.1, dtype=np.float32)
        arr[:, 0, 0] = -9999.0
        with rasterio.open(filename, "w", driver="GTiff", width=4, height=4, count=6, dtype="float32",
                           crs=crs, transform=Affine(10, 0, 500000, 0, -10, 4500000)) as dst:
            dst.write(arr)

    monkeypatch.setitem(sys.modules, "ee", fake_ee)
    monkeypatch.setitem(sys.modules, "geemap", types.SimpleNamespace(ee_export_image=ee_export_image))
    return calls


def test_ee_download_writes_both_files(tmp_path, monkeypatch):
    calls = _install_fakes(monkeypatch)
    grid = grid_from_point(41.0963, 14.4009, 500)
    windows = {"before": (date(2024, 6, 1), date(2024, 7, 31)), "after": (date(2025, 6, 1), date(2025, 7, 31))}
    paths = {"before": tmp_path / "b.tif", "after": tmp_path / "a.tif"}
    out = download_sentinel_pair_ee(grid, windows, paths, project="my-project")
    assert all(p.exists() for p in out.values())
    assert calls["project"] == "my-project"
    assert [e[2] for e in calls["exports"]] == [grid.crs, grid.crs]


def test_ee_masked_pixels_become_nan(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    grid = grid_from_point(41.0963, 14.4009, 500)
    out = download_sentinel_pair_ee(grid, {"before": (date(2024, 6, 1), date(2024, 7, 31))}, {"before": tmp_path / "b.tif"}, project="p")
    with rasterio.open(out["before"]) as src:
        arr = src.read()
        assert np.isnan(src.nodata)
        assert src.descriptions[0] == "B02 blue"
    assert np.isnan(arr[:, 0, 0]).all()
    assert np.allclose(arr[:, 1:, 1:], 0.1)


def test_ee_reports_failed_export(tmp_path, monkeypatch):
    calls = _install_fakes(monkeypatch)
    calls["fail_export"] = True
    grid = grid_from_point(41.0963, 14.4009, 500)
    with pytest.raises(RuntimeError, match="export"):
        download_sentinel_pair_ee(grid, {"before": (date(2024, 6, 1), date(2024, 7, 31))}, {"before": tmp_path / "b.tif"}, project="p")


def test_ee_needs_a_project(tmp_path, monkeypatch):
    _install_fakes(monkeypatch)
    grid = grid_from_point(41.0963, 14.4009, 500)
    with pytest.raises(ValueError, match="project id"):
        download_sentinel_pair_ee(grid, {}, {}, project=None)


def test_ee_reports_empty_windows(tmp_path, monkeypatch):
    _install_fakes(monkeypatch, size=0)
    grid = grid_from_point(41.0963, 14.4009, 500)
    with pytest.raises(RuntimeError, match="No Sentinel-2 scenes"):
        download_sentinel_pair_ee(grid, {"before": (date(2024, 6, 1), date(2024, 6, 2))}, {"before": tmp_path / "b.tif"}, project="p")
