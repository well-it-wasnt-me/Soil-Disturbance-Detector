"""Unsupervised scan for new dump sites in an area of interest.

Needs no labels, no training data and no prior knowledge of where anything
is. Point it at a place and a time window, and it tells you which patches of
ground stopped behaving like they did in the same season of previous years.

What it will NOT find: dumps that were already there before the baseline
started. To this scanner those are just part of the scenery. Old sites need a
different tool, and pretending otherwise would waste somebody's afternoon.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from dgd.anomaly import COMPONENTS, dig_score, directional_z, regional_offsets, score_block, seasonal_windows
from dgd.aoi import Grid
from dgd.candidates import (
    Candidate,
    appearance_window,
    assign_ids,
    candidate_timeseries,
    chip_window,
    event_window,
    find_candidates,
)
from dgd.landcover import DEFAULT_EXCLUDE, load_worldcover
from dgd.progress import NullProgress
from dgd.stac import Acquisition, thin_acquisitions

log = logging.getLogger(__name__)


class SceneSource(Protocol):
    """Anything that can find and fetch cloud-masked reflectance for a grid."""

    failures: list[str]

    def search(self, grid: Grid, start: date, end: date) -> list[Acquisition]: ...

    def fetch_all(self, acqs: Sequence[Acquisition], grid: Grid, on_done=None) -> dict[str, np.ndarray]: ...

    def describe(self) -> dict: ...


@dataclass
class ScanConfig:
    grid: Grid
    target_start: date
    target_end: date
    output_dir: Path
    baseline_years: int = 3
    min_score: float = 3.0
    min_event_score: float = 4.0
    min_persistence: float = 0.5
    min_event_span_days: int = 14
    max_bad_pass_fraction: float = 0.05
    min_area_m2: float = 300.0
    min_baseline_obs: int = 6
    min_target_obs: int = 3
    exclude_landcover: tuple[int, ...] = DEFAULT_EXCLUDE
    aoi_mask: np.ndarray | None = None
    block_size: int = 256
    max_chips: int = 30
    max_passes_per_fortnight: int = 5
    regional_normalization: bool = True
    detect_transient: bool = True
    cache_dir: Path | None = None
    name: str = ""


@dataclass
class ScanResult:
    config: ScanConfig
    candidates: list[Candidate]
    baseline: list[Acquisition]
    target: list[Acquisition]
    rasters: dict[str, np.ndarray]
    labels: np.ndarray
    landcover: np.ndarray
    started: datetime
    finished: datetime | None = None
    source: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    bad_passes: list[str] = field(default_factory=list)
    outputs: dict[str, Path] = field(default_factory=dict)


def _search_windows(source: SceneSource, grid: Grid, windows: Sequence[tuple[date, date]], keep: int = 0) -> list[Acquisition]:
    seen: dict[str, Acquisition] = {}
    for start, end in windows:
        for acq in thin_acquisitions(source.search(grid, start, end), keep=keep):
            seen.setdefault(acq.key, acq)
    return sorted(seen.values(), key=lambda a: (a.date, a.platform))


def _area_survey(
    arrays, baseline, target, grid: Grid, strata: np.ndarray, n_strata: int, config: "ScanConfig"
) -> tuple[dict[str, np.ndarray] | None, np.ndarray]:
    """Look at the whole area once, on a strided sample, before scoring blocks.

    Returns per-scene land cover offsets (the regional drift) and a flag per
    target pass that is too weird to trust for transient events. Sampling
    every few pixels keeps memory sane. Sixty thousand pixels is plenty, and
    nobody needs four million witnesses to agree it was a dry summer.
    """
    stride = max(1, int(np.sqrt(grid.width * grid.height / 60000)))
    sl = (slice(None), slice(None, None, stride), slice(None, None, stride))
    base = np.stack([np.asarray(arrays[a.key][sl]) for a in baseline])
    tgt = np.stack([np.asarray(arrays[a.key][sl]) for a in target])
    z, ndvi = directional_z(base, tgt)
    sub_strata = strata[::stride, ::stride]
    offsets = None
    if config.regional_normalization:
        offsets = regional_offsets(z, sub_strata, n_strata)
        z = {k: v - offsets[k][:, sub_strata] for k, v in z.items()}
    dig = dig_score(z, ndvi)[len(baseline):]
    with np.errstate(invalid="ignore"):
        hot = (dig >= config.min_event_score).sum(axis=(1, 2))
    clear = np.isfinite(dig).sum(axis=(1, 2))
    frac = np.where(clear > 0, hot / np.maximum(clear, 1), 0.0)
    # When a big slice of the whole area lights up on the same day, the
    # ground did not all get dug up at once. The pass is bad. Haze, smoke,
    # a cloud the mask missed. It sits out transient detection.
    bad = frac > config.max_bad_pass_fraction
    for a, f in zip(target, frac):
        if f > config.max_bad_pass_fraction:
            log.info("Pass %s looks off (%.0f%% of the area hot), ignored for transient events", a.key, 100 * f)
    return offsets, bad


def _post_change_composites(cands: Sequence[Candidate], arrays, target, config: "ScanConfig") -> None:
    """RGB median of only the passes after each lead appeared, for its image clip.

    The window median hides anything that showed up late. A dump that
    arrived six weeks before the end of a six month window barely registers
    in it, and a clip that shows nothing convinces nobody. So each clip gets
    its own "after": the passes from first sighting onward (or to the last
    bare pass, for transient events).
    """
    import warnings

    for c in cands:
        if c.first_seen is None:
            continue
        hi = c.last_seen if (c.kind == "transient" and c.last_seen) else config.target_end
        keys = [a.key for a in target if c.first_seen <= a.date <= hi]
        if not keys:
            continue
        rs, cs = chip_window(c, config.grid.shape)
        stack = np.stack([np.asarray(arrays[k][[2, 1, 0], rs, cs], dtype=np.float32) for k in keys])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            c.after_rgb = np.nanmedian(stack, axis=0).astype(np.float32)
        c.after_label = f"After: median of {len(keys)} passes, {c.first_seen} to {hi}"


def run_scan(config: ScanConfig, source: SceneSource, progress=None, landcover_loader=load_worldcover) -> ScanResult:
    """Run the full scan and return results. Writing files is ``dgd.report``'s job."""

    progress = progress or NullProgress()
    started = datetime.now(timezone.utc)
    grid = config.grid
    t0 = time.monotonic()

    progress.step("Searching imagery")
    windows = seasonal_windows(config.target_start, config.target_end, config.baseline_years)
    base_acqs = _search_windows(source, grid, windows, config.max_passes_per_fortnight)
    tgt_acqs = _search_windows(source, grid, [(config.target_start, config.target_end)], config.max_passes_per_fortnight)
    if not tgt_acqs:
        raise RuntimeError("No Sentinel-2 passes in the target window. Widen it or relax the cloud limit.")
    if not base_acqs:
        raise RuntimeError("No Sentinel-2 passes in the baseline years. Sentinel-2 starts in mid 2015, the time machine is extra.")

    progress.step(f"Fetching {len(base_acqs) + len(tgt_acqs)} passes")
    tick, done = progress.counter(len(base_acqs) + len(tgt_acqs), "passes")
    arrays = source.fetch_all(base_acqs + tgt_acqs, grid, on_done=lambda _a: tick())
    done()
    baseline = [a for a in base_acqs if a.key in arrays]
    target = [a for a in tgt_acqs if a.key in arrays]
    log.info("Usable passes: %d baseline, %d target (fetch took %.0fs)", len(baseline), len(target), time.monotonic() - t0)
    if len(target) < config.min_target_obs:
        raise RuntimeError(
            f"Only {len(target)} usable target passes, need {config.min_target_obs}. "
            "Clouds won. Widen the target window or lower --min-target-obs."
        )
    if len(baseline) < config.min_baseline_obs:
        raise RuntimeError(
            f"Only {len(baseline)} usable baseline passes, need {config.min_baseline_obs}. "
            "Add --baseline-years or lower --min-baseline-obs."
        )

    progress.step("Loading land cover")
    try:
        landcover = landcover_loader(grid, config.cache_dir)
    except Exception as exc:
        log.warning("Land cover failed, carrying on without it: %s", exc)
        landcover = np.zeros(grid.shape, dtype=np.uint8)

    # Land cover classes become strata 0..K-1 for the regional correction.
    codes = np.unique(landcover)
    strata = np.searchsorted(codes, landcover).astype(np.int16)

    progress.step("Surveying the area (regional drift, bad passes)")
    offsets, bad_target = _area_survey(arrays, baseline, target, grid, strata, len(codes), config)
    target_days = np.array([(a.date - config.target_start).days for a in target])

    progress.step("Scoring change against the seasonal baseline")
    shape = grid.shape
    names = ("score", "material", "burn", "persistence", "event", *COMPONENTS)
    rasters = {name: np.full(shape, np.nan, dtype=np.float32) for name in names}
    rasters["n_baseline"] = np.zeros(shape, dtype=np.int16)
    rasters["n_target"] = np.zeros(shape, dtype=np.int16)
    rasters["event_idx"] = np.full(shape, -1, dtype=np.int32)
    rgb_baseline = np.full((3, *shape), np.nan, dtype=np.float32)
    rgb_target = np.full((3, *shape), np.nan, dtype=np.float32)

    work = Path(tempfile.mkdtemp(prefix="dgd_scan_"))
    try:
        all_acqs = baseline + target
        scene_scores = np.lib.format.open_memmap(work / "scene_scores.npy", mode="w+", dtype=np.float16, shape=(len(all_acqs), *shape))
        dig_scores = np.lib.format.open_memmap(work / "dig_scores.npy", mode="w+", dtype=np.float16, shape=(len(all_acqs), *shape))
        blocks = list(grid.blocks(config.block_size))
        tick, done = progress.counter(len(blocks), "blocks")
        for rs, cs in blocks:
            base_stack = np.stack([np.asarray(arrays[a.key][:, rs, cs]) for a in baseline])
            tgt_stack = np.stack([np.asarray(arrays[a.key][:, rs, cs]) for a in target])
            bs = score_block(
                base_stack,
                tgt_stack,
                min_score=config.min_score,
                min_baseline_obs=config.min_baseline_obs,
                min_target_obs=config.min_target_obs,
                strata=strata[rs, cs],
                offsets=offsets,
                target_days=target_days,
                min_event_span_days=config.min_event_span_days,
                bad_target=bad_target,
            )
            for name in ("score", "material", "burn", "persistence", "event", "event_idx", "n_baseline", "n_target"):
                rasters[name][rs, cs] = getattr(bs, name)
            for k in COMPONENTS:
                rasters[k][rs, cs] = bs.components[k]
            rgb_baseline[:, rs, cs] = bs.rgb_baseline
            rgb_target[:, rs, cs] = bs.rgb_target
            with np.errstate(over="ignore", invalid="ignore"):
                scene_scores[:, rs, cs] = np.clip(bs.scene_scores, -1000, 1000).astype(np.float16)
                dig_scores[:, rs, cs] = np.clip(bs.dig_scores, -1000, 1000).astype(np.float16)
            tick()
        done()
        scene_scores.flush()
        dig_scores.flush()

        exclude = np.isin(landcover, config.exclude_landcover) if config.exclude_landcover else np.zeros(shape, dtype=bool)
        if config.aoi_mask is not None:
            exclude |= ~config.aoi_mask

        progress.step("Extracting candidate sites")
        with np.errstate(invalid="ignore"):
            persistent = (rasters["score"] >= config.min_score) & (rasters["persistence"] >= config.min_persistence) & ~exclude
        common = dict(
            persistence=rasters["persistence"],
            material=rasters["material"],
            burn=rasters["burn"],
            components={k: rasters[k] for k in COMPONENTS},
            landcover=landcover,
            min_area_m2=config.min_area_m2,
        )
        labels, cands = find_candidates(grid, persistent, rasters["score"], kind="persistent", **common)
        if config.detect_transient:
            with np.errstate(invalid="ignore"):
                transient = (rasters["event"] >= config.min_event_score) & ~persistent & ~exclude
            offset = max((c.label for c in cands), default=0)
            t_labels, t_cands = find_candidates(grid, transient, rasters["event"], kind="transient", label_offset=offset, **common)
            labels = np.where(t_labels > 0, t_labels, labels)
            cands += t_cands
        cands = assign_ids(cands)

        dates = [a.date for a in all_acqs]
        for c in cands:
            c.timeseries = candidate_timeseries(c, labels, scene_scores if c.kind == "persistent" else dig_scores, dates)
            if c.kind == "persistent":
                c.last_clear, c.first_seen = appearance_window(c.timeseries, config.target_start, config.min_score)
            else:
                c.last_clear, c.first_seen, c.last_seen = event_window(c.timeseries, config.target_start, config.min_event_score)
        del scene_scores, dig_scores
        _post_change_composites(cands[: config.max_chips], arrays, target, config)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    rasters["rgb_baseline"] = rgb_baseline
    rasters["rgb_target"] = rgb_target
    log.info("Scan finished: %d candidates in %.0fs", len(cands), time.monotonic() - t0)
    return ScanResult(
        config=config,
        candidates=cands,
        baseline=baseline,
        target=target,
        rasters=rasters,
        labels=labels,
        landcover=landcover,
        started=started,
        finished=datetime.now(timezone.utc),
        source=source.describe(),
        failures=list(getattr(source, "failures", [])),
        bad_passes=[a.key for a, b in zip(target, bad_target) if b],
    )


__all__ = ["ScanConfig", "ScanResult", "SceneSource", "run_scan"]
