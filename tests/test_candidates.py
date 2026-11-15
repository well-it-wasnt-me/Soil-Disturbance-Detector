from datetime import date, timedelta

import numpy as np
import pytest
from shapely.geometry import Polygon, box

from dgd.anomaly import COMPONENTS
from dgd.candidates import (
    appearance_window,
    assign_ids,
    candidate_timeseries,
    event_window,
    find_candidates,
    rank_score,
    rectangularity,
)


def _inputs(shape):
    score = np.zeros(shape, dtype=np.float32)
    comps = {k: np.zeros(shape, dtype=np.float32) for k in COMPONENTS}
    return score, comps


def _find(grid, mask, score, comps, landcover=None, **kw):
    return find_candidates(
        grid,
        mask,
        score,
        persistence=np.ones(grid.shape, dtype=np.float32),
        material=score,
        burn=np.zeros(grid.shape, dtype=np.float32),
        components=comps,
        landcover=landcover if landcover is not None else np.full(grid.shape, 30, dtype=np.uint8),
        **kw,
    )


def test_patches_are_found_filtered_merged_and_described(small_grid):
    score, comps = _inputs(small_grid.shape)
    score[5:10, 5:10] = 6.0  # 25 px
    score[5:10, 11:13] = 6.0  # 10 px, one pixel gap from the first: same site
    score[30, 30] = 9.0  # 1 px: too small
    comps["bright_gain"][5:10, 5:13] = 3.0
    mask = score >= 3
    labels, cands = _find(small_grid, mask, score, comps, min_area_m2=300)
    assert len(cands) == 1
    c = cands[0]
    assert c.n_px == 35 and c.area_m2 == pytest.approx(3500)
    assert c.signature == "brightening" and c.landcover_name == "grassland"
    assert (labels == c.label).sum() == 35
    assert c.geometry.area == pytest.approx(3500)
    assert -90 < c.lat < 90 and "google.com/maps" in c.maps_url and "openstreetmap" in c.osm_url
    # Without merging, the gap splits it in two.
    _, split = _find(small_grid, mask, score, comps, min_area_m2=200, merge_px=0)
    assert len(split) == 2


def test_nothing_found_returns_empty(small_grid):
    score, comps = _inputs(small_grid.shape)
    labels, cands = _find(small_grid, score > 1, score, comps)
    assert cands == [] and labels.max() == 0


def test_ids_rank_each_kind_separately(small_grid):
    score, comps = _inputs(small_grid.shape)
    score[2:8, 2:8] = 5.0
    score[20:30, 20:30] = 8.0
    _, p = _find(small_grid, score > 1, score, comps, kind="persistent")
    ev = np.zeros(small_grid.shape, dtype=np.float32)
    ev[40:50, 40:50] = 20.0
    off = max(c.label for c in p)
    _, t = _find(small_grid, ev > 1, ev, comps, kind="transient", label_offset=off)
    ranked = assign_ids(p + t)
    assert [c.id for c in ranked] == ["P001", "P002", "T001"]
    assert ranked[0].score_mean == pytest.approx(8.0)
    assert ranked[2].signature == "soil exposure" and ranked[2].label > off


def test_cropland_and_field_shapes_rank_lower(small_grid):
    score, comps = _inputs(small_grid.shape)
    score[2:20, 2:20] = 5.0  # 3.24 ha square: field-like
    lc = np.full(small_grid.shape, 30, dtype=np.uint8)
    _, cands = _find(small_grid, score > 1, score, comps, landcover=lc)
    assert cands[0].field_like and cands[0].context_weight == 0.5
    lc[:] = 40
    score[:] = 0
    score[2:6, 2:6] = 5.0
    _, cands = _find(small_grid, score > 1, score, comps, landcover=lc)
    assert not cands[0].field_like and cands[0].context_weight == 0.5 and cands[0].landcover_name == "cropland"


def test_rank_and_rectangularity():
    assert rank_score(4, 1.0, 31) == pytest.approx(20.0)
    assert rank_score(4, 1.0, 31, 0.5) == pytest.approx(10.0)
    assert rectangularity(box(0, 0, 10, 5)) == pytest.approx(1.0)
    assert rectangularity(Polygon([(0, 0), (10, 0), (0, 10)])) == pytest.approx(0.5)


def test_timeseries_and_windows(small_grid):
    score, comps = _inputs(small_grid.shape)
    score[2:6, 2:6] = 5
    labels, cands = _find(small_grid, score > 1, score, comps)
    c = cands[0]
    dates = [date(2024, 1, 1) + timedelta(days=10 * i) for i in range(6)]
    stack = np.zeros((6, *small_grid.shape), dtype=np.float32)
    stack[3:, 2:6, 2:6] = 6
    stack[1, 2:6, 2:6] = np.nan  # cloudy
    ts = candidate_timeseries(c, labels, stack, dates)
    assert np.isnan(ts[1][1]) and ts[3][1] == pytest.approx(6)
    last_clear, first = appearance_window(ts, dates[2], 3.0)
    assert (last_clear, first) == (dates[2], dates[3])
    assert appearance_window([(d, 0.0) for d in dates], dates[0], 3.0) == (None, None)
    series = [(dates[0], 0.0), (dates[1], 5.0), (dates[2], 6.0), (dates[3], 1.0), (dates[4], 7.0), (dates[5], 0.0)]
    assert event_window(series, dates[0], 3.0) == (dates[0], dates[1], dates[2])
    assert event_window([(d, 0.0) for d in dates], dates[0], 3.0) == (None, None, None)
