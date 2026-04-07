import sys
import types
from pathlib import Path

from dgd import sentinel


class _FakePoint:
    def __init__(self, coords):
        self.coords = coords

    def buffer(self, _):
        return self

    def bounds(self):
        return self


class _FakeGeometry:
    @staticmethod
    def Point(coords):
        return _FakePoint(coords)


class _FakeFilter:
    @staticmethod
    def lt(name, value):
        return ("lt", name, value)


class _FakeImageCollection:
    def __init__(self, name):
        self.name = name

    def filterBounds(self, aoi):
        return self

    def filterDate(self, start, end):
        return self

    def filter(self, _):
        return self

    def select(self, _):
        return self

    def size(self):
        return types.SimpleNamespace(getInfo=lambda: 1)

    def median(self):
        return self

    def clip(self, _):
        return self


def _install_fakes(tmp_path):
    fake_ee = types.SimpleNamespace()
    fake_ee.Geometry = _FakeGeometry
    fake_ee.Filter = _FakeFilter
    fake_ee.ImageCollection = _FakeImageCollection
    fake_ee.Initialize = lambda project=None: None
    fake_ee.Authenticate = lambda: None

    def ee_export_image(image, filename, scale, region, file_per_band):
        Path(filename).touch()

    fake_geemap = types.SimpleNamespace(ee_export_image=ee_export_image)

    sys.modules["ee"] = fake_ee
    sys.modules["geemap"] = fake_geemap


def test_download_sentinel_pair_creates_files(tmp_path, monkeypatch):
    _install_fakes(tmp_path)

    args = types.SimpleNamespace(
        before_date="2025-01-01",
        after_date="2025-02-01",
        lat=0.0,
        long=0.0,
        buffer_m=1000,
        window_days=30,
        cloud_pct=10,
        scale=10,
        before_path=str(tmp_path / "before.tif"),
        after_path=str(tmp_path / "after.tif"),
    )

    sentinel.download_sentinel_pair(args)

    assert Path(args.before_path).exists()
    assert Path(args.after_path).exists()
