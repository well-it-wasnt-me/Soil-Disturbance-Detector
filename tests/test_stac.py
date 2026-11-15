from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from dgd.stac import (
    COLLECTION_C1,
    COLLECTION_LEGACY,
    EarthSearchSource,
    REFLECTANCE_BANDS,
    Scene,
    group_acquisitions,
    reflectance_offset,
    scene_from_item,
    thin_acquisitions,
)


def test_reflectance_offset_rules():
    # Collection 1 carries the offset and says so.
    assert reflectance_offset(COLLECTION_C1, {}, -0.1) == -0.1
    # Legacy with the offset already removed by Element84.
    assert reflectance_offset(COLLECTION_LEGACY, {"earthsearch:boa_offset_applied": True, "s2:processing_baseline": "05.09"}, -0.1) == 0.0
    # Legacy from before baseline 04.00 never had an offset.
    assert reflectance_offset(COLLECTION_LEGACY, {"s2:processing_baseline": "02.14"}, -0.1) == 0.0
    # Legacy, new baseline, offset not applied: subtract it ourselves.
    assert reflectance_offset(COLLECTION_LEGACY, {"s2:processing_baseline": "04.00"}, -0.1) == -0.1
    assert reflectance_offset(COLLECTION_LEGACY, {"s2:processing_baseline": "garbage"}, None) == 0.0


def _scene(sid, coll, day, tile="33TVF", platform="sentinel-2a", cloud=10.0):
    return Scene(
        id=sid,
        collection=coll,
        datetime=datetime(2024, 6, day, 10, tzinfo=timezone.utc),
        platform=platform,
        tile=tile,
        cloud_cover=cloud,
        hrefs={},
    )


def test_group_acquisitions_prefers_c1_then_newest_legacy():
    scenes = [
        _scene("S2A_33TVF_20240601_0_L2A", COLLECTION_LEGACY, 1),
        _scene("S2A_33TVF_20240601_1_L2A", COLLECTION_LEGACY, 1),
        _scene("S2A_T33TVF_20240601T100000_L2A", COLLECTION_C1, 1),
        _scene("S2A_33TVF_20240611_0_L2A", COLLECTION_LEGACY, 11),
        _scene("S2A_33TVF_20240611_1_L2A", COLLECTION_LEGACY, 11),
        _scene("S2A_33TVG_20240611_0_L2A", COLLECTION_LEGACY, 11, tile="33TVG", cloud=50),
    ]
    acqs = group_acquisitions(scenes)
    assert [a.date.day for a in acqs] == [1, 11]
    assert acqs[0].scene_ids == ["S2A_T33TVF_20240601T100000_L2A"]
    # Two tiles on the 11th, newest legacy wins per tile, clearest first.
    assert acqs[1].scene_ids == ["S2A_33TVF_20240611_1_L2A", "S2A_33TVG_20240611_0_L2A"]
    assert acqs[1].cloud_cover == 10.0
    assert acqs[1].key == "sentinel-2a_2024-06-11"


def _fake_item(collection, props, assets=REFLECTANCE_BANDS + ("scl",)):
    a = {b: SimpleNamespace(href=f"https://example/{b}.tif", extra_fields={"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}) for b in assets}
    return SimpleNamespace(
        id="ITEM",
        collection_id=collection,
        properties={"platform": "Sentinel-2B", "eo:cloud_cover": 12.5, "grid:code": "MGRS-33TVF", **props},
        datetime=datetime(2024, 6, 1, tzinfo=timezone.utc),
        assets=a,
    )


def test_scene_from_item():
    s = scene_from_item(_fake_item(COLLECTION_C1, {}))
    assert s.platform == "sentinel-2b" and s.tile == "MGRS-33TVF" and s.offset == -0.1 and s.scale == 0.0001
    assert set(s.hrefs) == set(REFLECTANCE_BANDS) | {"scl"}
    legacy = scene_from_item(_fake_item(COLLECTION_LEGACY, {"earthsearch:boa_offset_applied": True}))
    assert legacy.offset == 0.0
    assert scene_from_item(_fake_item(COLLECTION_C1, {}, assets=("red", "scl"))) is None


def test_fetch_uses_cache_and_remembers_empty_passes(tmp_path, monkeypatch, small_grid):
    from dgd import stac
    from conftest import make_acq

    calls = []

    def fake_read(acq, grid):
        calls.append(acq.key)
        if acq.date.day == 2:
            return None
        return np.full((6, *grid.shape), 0.1, dtype=np.float32)

    monkeypatch.setattr(stac, "read_acquisition", fake_read)
    src = EarthSearchSource(cache_dir=tmp_path, workers=2)
    from datetime import date

    acqs = [make_acq(date(2024, 6, 1)), make_acq(date(2024, 6, 2))]
    got = src.fetch_all(acqs, small_grid)
    assert set(got) == {acqs[0].key}
    assert got[acqs[0].key].shape == (6, *small_grid.shape)
    got2 = src.fetch_all(acqs, small_grid)
    assert set(got2) == {acqs[0].key}
    # Second round came from disk, including the "nothing usable" verdict.
    assert sorted(calls) == sorted(a.key for a in acqs)


def test_fetch_all_survives_failures(tmp_path, monkeypatch, small_grid):
    from datetime import date

    from dgd import stac
    from conftest import make_acq

    def boom(acq, grid):
        raise OSError("connection reset by an unhappy server")

    monkeypatch.setattr(stac, "read_acquisition", boom)
    src = EarthSearchSource(cache_dir=None)
    got = src.fetch_all([make_acq(date(2024, 6, 1))], small_grid)
    assert got == {}
    assert src.failures == ["sentinel-2a_2024-06-01"]
    assert src.describe()["provider"].startswith("Element84")


@pytest.mark.network
def test_live_earth_search_returns_passes():
    from datetime import date

    from dgd.aoi import grid_from_point
    from dgd.stac import read_acquisition

    grid = grid_from_point(41.0963, 14.4009, 300)
    acqs = EarthSearchSource().search(grid, date(2024, 6, 1), date(2024, 6, 30))
    assert len(acqs) >= 4
    arr = read_acquisition(min(acqs, key=lambda a: a.cloud_cover), grid)
    assert arr is not None and arr.shape == (6, *grid.shape)
    # Reflectance, not raw DN. Vegetation red sits well under 0.2.
    assert 0.0 < np.nanmedian(arr[2]) < 0.3


def test_thin_acquisitions_keeps_the_clearest_per_fortnight():
    from datetime import date, timedelta

    from dgd.stac import Acquisition

    acqs = []
    for i in range(28):  # a pass every day for four weeks
        d = date(2024, 6, 1) + timedelta(days=i)
        acqs.append(Acquisition(platform="sentinel-2a", date=d, scenes=(_scene(f"S{i}", COLLECTION_C1, 1, cloud=float(i % 7)),)))
    kept = thin_acquisitions(acqs, keep=3)
    assert len(kept) == 6
    assert all(a.cloud_cover <= 1 for a in kept)
    assert kept == sorted(kept, key=lambda a: a.date)
    assert thin_acquisitions(acqs, keep=0) == acqs


def test_fetch_gives_the_same_cached_array_on_first_and_later_runs(tmp_path, monkeypatch):
    from datetime import date

    from dgd import stac
    from dgd.aoi import grid_from_point
    from dgd.stac import Acquisition, EarthSearchSource

    grid = grid_from_point(41.0, 14.0, 100)
    acq = Acquisition(platform="sentinel-2a", date=date(2024, 6, 1), scenes=(_scene("S1", COLLECTION_C1, 1),))
    monkeypatch.setattr(stac, "read_acquisition", lambda a, g: np.full((6, *g.shape), 0.123456, dtype=np.float32))
    src = EarthSearchSource(cache_dir=tmp_path)
    first, again = src.fetch(acq, grid), src.fetch(acq, grid)
    assert isinstance(first, np.memmap) and first.dtype == np.float16
    np.testing.assert_array_equal(first, again)
