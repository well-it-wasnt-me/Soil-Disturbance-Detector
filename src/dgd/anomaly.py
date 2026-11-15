"""Change scoring against a seasonal baseline.

The idea is simple enough to explain to a judge. For every pixel we look at
the same months in previous years and learn what normal looks like there,
including how much it normally wobbles. Then we ask how far outside normal
the pixel sits now, in units of its own wobble (a robust z-score).

A field that gets plowed every April wobbles a lot every April, so plowing it
again is not news. A meadow that has been a meadow for five summers and is
now a pile of something is news.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import date

import numpy as np

from dgd.features import spectral_indices

# Robust spread below which we refuse to believe a pixel is that stable.
# Without a floor a perfectly calm pixel turns sensor noise into a 40 sigma
# "event", and the analyst reading the report turns into a skeptic.
SPREAD_FLOOR = {"ndvi": 0.05, "bsi": 0.03, "nbr": 0.05, "bright": 0.015}
MAD_TO_SIGMA = 1.4826

# Directional z-scores, signed so that positive always means "more like a dump".
COMPONENTS = ("veg_loss", "bare_gain", "bright_gain", "burn_gain")


def shift_years(d: date, years: int) -> date:
    """Same calendar day N years earlier (negative) or later. Feb 29 becomes Feb 28."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def seasonal_windows(target_start: date, target_end: date, years: int) -> list[tuple[date, date]]:
    """Baseline windows covering the same calendar span in prior years, oldest first."""
    if target_end < target_start:
        raise ValueError("Target window ends before it starts.")
    if (target_end - target_start).days > 366:
        raise ValueError("Target window longer than a year. Seasonal baselines stop meaning anything past that.")
    if years < 1:
        raise ValueError("Need at least one baseline year. Three is better.")
    return [(shift_years(target_start, -k), shift_years(target_end, -k)) for k in range(years, 0, -1)]


def step_median(scores: np.ndarray, days: np.ndarray, min_obs: int, min_span_days: int) -> tuple[np.ndarray, np.ndarray]:
    """Best "it changed at some point and stayed changed" score, per pixel.

    For every possible start pass t, take the median of all clear scores from
    t to the end of the window, provided there are at least ``min_obs`` of
    them spread over at least ``min_span_days``. Keep the best one.

    A plain median over the whole window punishes a dump for arriving late.
    If it showed up two months in, the two clean months before drag the
    median down and a 6 sigma pile reads like a 4. This asks the fairer
    question, and the requirement that the change holds to the end of the
    window keeps it honest.

    Returns (score, index of the starting pass), NaN and -1 where nothing
    qualified.
    """
    n = scores.shape[0]
    shape = scores.shape[1:]
    best = np.full(shape, -np.inf, dtype=np.float32)
    best_idx = np.full(shape, -1, dtype=np.int32)
    valid = np.isfinite(scores)
    # Clear passes remaining from t onward, and the day of the last clear pass.
    remaining = np.cumsum(valid[::-1], axis=0)[::-1]
    last_day = np.full(shape, -1, dtype=np.int64)
    for t in range(n):
        last_day[valid[t]] = days[t]
    for t in range(n):
        first_day = np.full(shape, np.iinfo(np.int64).max, dtype=np.int64)
        # The suffix effectively starts at the first clear pass at or after t.
        for u in range(t, n):
            unset = valid[u] & (first_day == np.iinfo(np.int64).max)
            first_day[unset] = days[u]
            if not (first_day == np.iinfo(np.int64).max).any():
                break
        ok = (remaining[t] >= min_obs) & (last_day - first_day >= min_span_days)
        if not ok.any():
            continue
        med = _nanmedian(scores[t:])
        better = ok & (med > best)
        best[better] = med[better]
        best_idx[better] = t
    best[~np.isfinite(best)] = np.nan
    return best, best_idx


@dataclass
class BlockScores:
    """Scores for one block of the grid. All 2D arrays are (rows, cols)."""

    score: np.ndarray
    material: np.ndarray
    burn: np.ndarray
    persistence: np.ndarray
    n_baseline: np.ndarray
    n_target: np.ndarray
    components: dict[str, np.ndarray]
    event: np.ndarray  # strongest dig signal that lasted at least the minimum span
    event_idx: np.ndarray  # target pass index where that span starts, -1 if none
    scene_scores: np.ndarray  # (n_baseline + n_target scenes, rows, cols)
    dig_scores: np.ndarray  # same shape, excavation signature only
    rgb_baseline: np.ndarray  # (3, rows, cols) red, green, blue
    rgb_target: np.ndarray


def _nanmedian(a: np.ndarray, axis: int = 0) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(a, axis=axis).astype(np.float32)


def _directional_z(idx: dict[str, np.ndarray], base: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Turn per-scene indices into signed z-scores against the baseline."""
    med = {k: _nanmedian(base[k]) for k in base}
    spread = {}
    for k in base:
        mad = _nanmedian(np.abs(base[k] - med[k]))
        spread[k] = np.maximum(MAD_TO_SIGMA * mad, SPREAD_FLOOR[k])
    return {
        "veg_loss": (med["ndvi"] - idx["ndvi"]) / spread["ndvi"],
        "bare_gain": (idx["bsi"] - med["bsi"]) / spread["bsi"],
        "bright_gain": (idx["bright"] - med["bright"]) / spread["bright"],
        "burn_gain": (med["nbr"] - idx["nbr"]) / spread["nbr"],
    }


def directional_z(baseline: np.ndarray, target: np.ndarray) -> tuple[dict[str, np.ndarray], np.ndarray]:
    """Directional z-scores for every scene, baseline first then target.

    Inputs are reflectance stacks shaped ``(scenes, 6, rows, cols)``. Returns
    the z dict with ``(scenes, rows, cols)`` arrays plus the raw NDVI stack,
    which the dig score needs for its bare soil check.
    """
    nb = baseline.shape[0]
    allscenes = np.concatenate([baseline, target], axis=0).astype(np.float32, copy=False)
    idx = spectral_indices(np.moveaxis(allscenes, 1, 0))
    return _directional_z(idx, {k: v[:nb] for k, v in idx.items()}), idx["ndvi"]


# Strata smaller than this (in sampled pixels) borrow the area-wide offset.
# Taking the median of twelve pixels and calling it "the region" is how
# statistics get a bad name.
MIN_STRATUM_PX = 200


def regional_offsets(z: dict[str, np.ndarray], strata: np.ndarray, n_strata: int) -> dict[str, np.ndarray]:
    """Per-scene, per-land-cover median of each z component.

    A drought, a wet spring or a hazy day moves every pixel of the same land
    cover together. A dump moves one patch. Subtracting the shared movement
    leaves the local one standing, which is the whole point.

    Parameters
    ----------
    z : dict
        Component name to ``(scenes, rows, cols)`` arrays, usually computed on
        a strided subsample of the full area.
    strata : np.ndarray
        ``(rows, cols)`` integer stratum index in ``[0, n_strata)``.

    Returns
    -------
    dict
        Component name to ``(scenes, n_strata)`` offsets.
    """
    out = {}
    flat_strata = strata.ravel()
    for k, arr in z.items():
        n = arr.shape[0]
        flat = arr.reshape(n, -1)
        overall = _nanmedian(flat, axis=1)
        offs = np.repeat(overall[:, None], n_strata, axis=1)
        for s in range(n_strata):
            sel = flat_strata == s
            if sel.sum() < MIN_STRATUM_PX:
                continue
            sub = flat[:, sel]
            enough = np.isfinite(sub).sum(axis=1) >= MIN_STRATUM_PX
            offs[enough, s] = _nanmedian(sub[enough], axis=1)
        out[k] = np.nan_to_num(offs, nan=0.0).astype(np.float32)
    return out


# You cannot bury anything without exposing dirt. Mowed hay, drought-brown
# pasture and stubble all sit around NDVI 0.4 to 0.6. Freshly turned soil
# sits well under 0.3. Checked on real passes, not taken on faith.
BARE_NDVI_MAX = 0.3


def dig_score(z: dict[str, np.ndarray], ndvi: np.ndarray | None = None) -> np.ndarray:
    """Excavation signature: vegetation gone and bare soil exposed, nothing else.

    Brightness is left out on purpose. Haze brightens everything, but it
    lifts blue more than SWIR, which pushes the bare soil index down. So a
    hazy day scores low here while a freshly backfilled pit scores high.

    If ``ndvi`` is given, passes where the pixel is not actually bare
    (NDVI at or above ``BARE_NDVI_MAX``) are capped at zero. Relative change
    alone happily flags a hay field after mowing. Absolute bareness does not.
    """
    with np.errstate(invalid="ignore"):
        dig = ((z["veg_loss"] + z["bare_gain"]) / math.sqrt(2.0)).astype(np.float32)
        if ndvi is not None:
            not_bare = ndvi >= BARE_NDVI_MAX
            dig = np.where(not_bare, np.fmin(dig, 0.0), dig).astype(np.float32)
    return dig


def event_strength(
    scores: np.ndarray,
    days: np.ndarray,
    min_span_days: int = 14,
) -> tuple[np.ndarray, np.ndarray]:
    """Strongest anomaly that lasted at least ``min_span_days``, per pixel.

    For every clear pass i, follow the clear passes after it until one lands
    at least ``min_span_days`` later, and take the minimum score along the
    way. The event strength is the best such minimum. A hazy afternoon lasts
    a day. A backfilled pit stays bare for weeks. Hay grows back in two or
    three, which is why the default is two weeks and why hay still sneaks in
    sometimes.

    Parameters
    ----------
    scores : np.ndarray
        ``(passes, rows, cols)`` per-pass scores, NaN when cloudy.
    days : np.ndarray
        Day number of each pass, ascending.

    Returns
    -------
    tuple
        (strength, index of the starting pass), NaN and -1 where no span
        long enough had clear looks at both ends.
    """
    n = scores.shape[0]
    shape = scores.shape[1:]
    best = np.full(shape, -np.inf, dtype=np.float32)
    best_idx = np.full(shape, -1, dtype=np.int32)
    for i in range(n):
        valid_i = np.isfinite(scores[i])
        if not valid_i.any():
            continue
        run_min = scores[i].copy()
        reached = np.zeros(shape, dtype=bool)
        for j in range(i + 1, n):
            ok = valid_i & ~reached & np.isfinite(scores[j])
            if not ok.any():
                continue
            run_min[ok] = np.fmin(run_min[ok], scores[j][ok])
            if days[j] - days[i] >= min_span_days:
                reached |= ok
                if (reached | ~valid_i).all():
                    break
        cand = np.where(reached, run_min, -np.inf)
        better = cand > best
        best[better] = cand[better]
        best_idx[better] = i
    best[~np.isfinite(best)] = np.nan
    return best, best_idx


def score_block(
    baseline: np.ndarray,
    target: np.ndarray,
    min_score: float = 3.0,
    min_baseline_obs: int = 6,
    min_target_obs: int = 3,
    strata: np.ndarray | None = None,
    offsets: dict[str, np.ndarray] | None = None,
    target_days: np.ndarray | None = None,
    min_event_span_days: int = 14,
    bad_target: np.ndarray | None = None,
) -> BlockScores:
    """Score one block.

    Parameters
    ----------
    baseline, target : np.ndarray
        Reflectance stacks shaped ``(scenes, 6, rows, cols)``, NaN where
        cloudy. Band order as in ``dgd.stac.REFLECTANCE_BANDS``.
    min_score : float
        Per-scene score that counts as "anomalous" for persistence.
    min_baseline_obs, min_target_obs : int
        Pixels with fewer clear looks than this get NaN. Judging a pixel on
        two cloudy glimpses is how you end up apologizing to a farmer.
    strata, offsets : optional
        Land cover stratum per pixel and the per-scene offsets from
        ``regional_offsets``. When given, shared regional movement is removed
        before scoring.
    target_days, min_event_span_days, bad_target : optional
        Day numbers of the target passes, the minimum duration of a transient
        event, and a boolean per target pass marking passes too hazy or weird
        to trust for event detection.

    Notes
    -----
    Two signatures are scored per scene, each a Stouffer combination of
    three directional z-scores:

    ``material`` = vegetation loss + bare soil gain + brightening. Fresh
    rubble, plastic, construction waste, scraped ground.

    ``burn`` = vegetation loss + burn ratio drop + darkening. Waste fires.

    The block score is the larger of the two step medians (see
    ``step_median``): the change has to appear at some point in the target
    window and then be there in at least half the clear looks until the end.
    That quietly kills one-off cloud edges and the odd parked truck.
    """
    nb, nt = baseline.shape[0], target.shape[0]
    if nb == 0 or nt == 0:
        raise ValueError("Need at least one baseline and one target scene with clear pixels.")

    allscenes = np.concatenate([baseline, target], axis=0).astype(np.float32, copy=False)
    idx = spectral_indices(np.moveaxis(allscenes, 1, 0))
    base_idx = {k: v[:nb] for k, v in idx.items()}
    z = _directional_z(idx, base_idx)
    if offsets is not None and strata is not None:
        for k in COMPONENTS:
            z[k] = z[k] - offsets[k][:, strata]

    root3 = math.sqrt(3.0)
    material_s = (z["veg_loss"] + z["bare_gain"] + z["bright_gain"]) / root3
    burn_s = (z["veg_loss"] + z["burn_gain"] - z["bright_gain"]) / root3
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        scene_scores = np.fmax(material_s, burn_s).astype(np.float32)

    n_baseline = np.isfinite(idx["ndvi"][:nb]).sum(axis=0).astype(np.int16)
    tgt_scores = scene_scores[nb:]
    n_target = np.isfinite(tgt_scores).sum(axis=0).astype(np.int16)

    days = np.asarray(target_days) if target_days is not None else np.arange(nt) * 5
    step_obs = max(min_target_obs, 3)
    material, mat_start = step_median(material_s[nb:], days, step_obs, min_event_span_days)
    burn, burn_start = step_median(burn_s[nb:], days, step_obs, min_event_span_days)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        score = np.fmax(material, burn).astype(np.float32)
        start = np.where(np.nan_to_num(burn, nan=-np.inf) > np.nan_to_num(material, nan=-np.inf), burn_start, mat_start)
        # Several start dates can tie on the median. Snap to the first pass at
        # or after the chosen start that actually crosses the threshold, so
        # the step begins where the change does.
        t_idx = np.arange(nt)[:, None, None]
        crossing = (t_idx >= start[None]) & (np.nan_to_num(tgt_scores, nan=-np.inf) >= min_score)
        start = np.where(crossing.any(axis=0), crossing.argmax(axis=0), start)
        # Persistence is measured from the step onward, not over the whole
        # window. Otherwise the clean months before the dump count against it.
        after_step = t_idx >= start[None]
        clear = np.isfinite(tgt_scores) & after_step
        hits = (clear & (tgt_scores >= min_score)).sum(axis=0)
        n_after = clear.sum(axis=0)
        persistence = np.where(n_after > 0, hits / np.maximum(n_after, 1), np.nan).astype(np.float32)

    unreliable = (n_baseline < min_baseline_obs) | (n_target < min_target_obs)
    for arr in (score, material, burn, persistence):
        arr[unreliable] = np.nan

    dig_s = dig_score(z, idx["ndvi"])
    if bad_target is not None and bad_target.any():
        # Blank the bad passes in the stored scores too, not only for event
        # detection. Otherwise the candidate timeline can date a dig to a
        # hazy day that detection itself refused to trust.
        dig_s[nb:][np.asarray(bad_target, dtype=bool)] = np.nan
    tgt_dig = dig_s[nb:]
    event, event_idx = event_strength(tgt_dig, days, min_event_span_days)
    event[n_baseline < min_baseline_obs] = np.nan

    # Components describe the pixel after its step, same as persistence. A
    # median over the whole window would let the quiet months before the
    # change water down the very numbers the analyst reads.
    components = {k: _nanmedian(np.where(after_step, z[k][nb:], np.nan)) for k in COMPONENTS}
    for arr in components.values():
        arr[unreliable] = np.nan

    rgb_idx = [2, 1, 0]
    rgb_baseline = _nanmedian(allscenes[:nb, rgb_idx], axis=0)
    rgb_target = _nanmedian(allscenes[nb:, rgb_idx], axis=0)

    return BlockScores(
        score=score,
        material=material,
        burn=burn,
        persistence=persistence,
        n_baseline=n_baseline,
        n_target=n_target,
        components=components,
        event=event,
        event_idx=event_idx,
        scene_scores=scene_scores,
        dig_scores=dig_s,
        rgb_baseline=rgb_baseline,
        rgb_target=rgb_target,
    )


__all__ = [
    "BARE_NDVI_MAX",
    "BlockScores",
    "COMPONENTS",
    "dig_score",
    "directional_z",
    "event_strength",
    "regional_offsets",
    "score_block",
    "seasonal_windows",
    "shift_years",
    "step_median",
]
