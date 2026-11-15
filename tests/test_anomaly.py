from datetime import date

import numpy as np
import pytest

from conftest import GRASS, SOIL, WASTE
from dgd.anomaly import (
    BARE_NDVI_MAX,
    dig_score,
    directional_z,
    event_strength,
    regional_offsets,
    score_block,
    seasonal_windows,
    shift_years,
    step_median,
)


def _stack(n, spectrum, shape=(4, 4), noise=0.02, seed=0):
    rng = np.random.default_rng(seed)
    base = np.broadcast_to(spectrum[None, :, None, None], (n, 6, *shape)).astype(np.float32)
    return base * rng.normal(1.0, noise, size=(n, 1, *shape)).astype(np.float32)


def test_shift_years_and_windows():
    assert shift_years(date(2024, 2, 29), -1) == date(2023, 2, 28)
    w = seasonal_windows(date(2025, 4, 1), date(2025, 9, 30), 3)
    assert w == [(date(2022, 4, 1), date(2022, 9, 30)), (date(2023, 4, 1), date(2023, 9, 30)), (date(2024, 4, 1), date(2024, 9, 30))]
    with pytest.raises(ValueError):
        seasonal_windows(date(2025, 1, 1), date(2026, 6, 1), 3)
    with pytest.raises(ValueError):
        seasonal_windows(date(2025, 6, 1), date(2025, 1, 1), 3)
    with pytest.raises(ValueError):
        seasonal_windows(date(2025, 1, 1), date(2025, 6, 1), 0)


def test_new_waste_on_stable_grass_scores_high_and_persists():
    base = _stack(30, GRASS)
    tgt = _stack(12, GRASS, seed=1)
    tgt[4:, :, :2, :2] = WASTE[:, None, None]  # waste from pass 4 onward
    bs = score_block(base, tgt, target_days=np.arange(12) * 5)
    assert bs.score[:2, :2].min() > 5
    assert bs.persistence[:2, :2].min() >= 0.9
    assert np.nanmax(bs.score[2:, 2:]) < 3
    assert bs.n_baseline.min() == 30 and bs.n_target.min() == 12


def test_change_that_happens_every_year_is_not_news():
    # Pixel bare in a third of the baseline passes (annual plowing), bare again now.
    base = _stack(30, GRASS)
    base[::3, :, :, :] = SOIL[:, None, None]
    tgt = _stack(9, GRASS, seed=1)
    tgt[::3] = SOIL[:, None, None]
    bs = score_block(base, tgt, target_days=np.arange(9) * 5)
    assert np.nanmax(bs.score) < 3


def test_too_few_clear_looks_gives_nan():
    base = _stack(10, GRASS)
    base[:, :, 0, 0] = np.nan
    tgt = _stack(5, GRASS)
    bs = score_block(base, tgt, min_baseline_obs=6, target_days=np.arange(5) * 5)
    assert np.isnan(bs.score[0, 0]) and np.isfinite(bs.score[1, 1])
    with pytest.raises(ValueError):
        score_block(base[:0], tgt)


def test_step_median_rewards_late_arrivals_fairly():
    scores = np.zeros((10, 1, 1), dtype=np.float32)
    scores[6:] = 6.0  # arrived late, stayed
    days = np.arange(10) * 5
    best, idx = step_median(scores, days, min_obs=3, min_span_days=10)
    assert best[0, 0] == pytest.approx(6.0) and idx[0, 0] <= 6
    # A plain median over the window would have said 0. That is the point.
    assert np.median(scores) == 0.0
    # A single late spike is not a step.
    scores[:] = 0
    scores[-1] = 9
    best, _ = step_median(scores, days, min_obs=3, min_span_days=10)
    assert best[0, 0] < 3


def test_event_needs_to_last():
    days = np.array([0, 3, 6, 10, 20, 30, 40])
    s = np.zeros((7, 1, 3), dtype=np.float32)
    s[1:3, 0, 0] = 9.0  # 3 days of "event": haze-ish, too short
    s[2:5, 0, 1] = 6.0  # day 6 to day 20, 14 days: counts
    s[2:5, 0, 2] = 6.0
    s[3, 0, 2] = np.nan  # a cloud in the middle does not break it
    strength, idx = event_strength(s, days, min_span_days=14)
    assert strength[0, 0] < 1
    assert strength[0, 1] == pytest.approx(6.0) and idx[0, 1] == 2
    assert strength[0, 2] == pytest.approx(6.0)


def test_dig_score_requires_actual_bare_soil():
    z = {"veg_loss": np.array([6.0, 6.0]), "bare_gain": np.array([6.0, 6.0])}
    ndvi = np.array([0.15, BARE_NDVI_MAX + 0.2])  # bare soil, mowed hay
    d = dig_score(z, ndvi)
    assert d[0] > 8 and d[1] <= 0


def test_dig_and_cover_shows_up_as_event_not_persistent():
    base = _stack(30, GRASS)
    tgt = _stack(14, GRASS, seed=2)
    tgt[3:8, :, :2, :2] = SOIL[:, None, None]  # bare for 5 passes, then grass again
    bs = score_block(base, tgt, target_days=np.arange(14) * 5, min_event_span_days=14)
    assert bs.event[:2, :2].min() > 4
    assert np.nanmax(bs.score[:2, :2]) < 3  # healed, so not persistent
    assert np.nanmax(bs.event[2:, 2:]) < 4


def test_hazy_pass_is_not_an_event():
    base = _stack(30, GRASS)
    tgt = _stack(10, GRASS, seed=3)
    tgt[5, :3] += 0.08  # blue, green, red lifted for one pass
    bs = score_block(base, tgt, target_days=np.arange(10) * 5)
    assert np.nanmax(bs.event) < 4
    assert np.nanmax(bs.score) < 3


def test_regional_offsets_remove_shared_drift():
    base = _stack(20, GRASS, shape=(20, 20))
    tgt = _stack(6, GRASS, shape=(20, 20), seed=4)
    tgt[:, 3] *= 0.7  # everybody's NIR drops: drought
    z, _ = directional_z(base, tgt)
    strata = np.zeros((20, 20), dtype=np.int16)
    offs = regional_offsets(z, strata, 1)
    assert offs["veg_loss"].shape == (26, 1)
    assert offs["veg_loss"][20:, 0].min() > 1  # the drought was measured
    raw = score_block(base, tgt, target_days=np.arange(6) * 5)
    fixed = score_block(base, tgt, target_days=np.arange(6) * 5, strata=strata, offsets=offs)
    assert np.nanmedian(raw.components["veg_loss"]) > 1
    assert abs(np.nanmedian(fixed.components["veg_loss"])) < 0.5


def test_bad_target_passes_are_ignored_for_events():
    base = _stack(30, GRASS)
    tgt = _stack(8, GRASS, seed=5)
    tgt[2:6] = SOIL[:, None, None]  # whole block bare for a while
    bad = np.zeros(8, dtype=bool)
    bad[2:6] = True
    bs = score_block(base, tgt, target_days=np.arange(8) * 5, bad_target=bad)
    assert np.nanmax(bs.event) < 4
    # The stored per-pass dig scores, which date the event in the report,
    # must not keep the passes detection refused to trust.
    assert np.isnan(bs.dig_scores[30 + 2:30 + 6]).all()
    assert np.isfinite(bs.dig_scores[30 + 6:]).any()
