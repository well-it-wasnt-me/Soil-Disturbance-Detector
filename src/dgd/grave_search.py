"""One grave search, start to finish: load, align, detect, rank.

Report writing lives in ``dgd.grave_report``. This module only does the
thinking, so tests and the evaluation can call it without making files.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from dgd.graves import Evidence, GraveCandidate, find_graves, gather_evidence
from dgd.highres import HRImage, coregister, grid_for_raster, match_histograms, read_to_grid
from dgd.progress import NullProgress

log = logging.getLogger(__name__)


@dataclass
class GraveSearchConfig:
    after_path: str
    output_dir: Path
    before_path: str | None = None
    dsm_path: str | None = None
    thermal_path: str | None = None
    bands: str | None = None
    before_bands: str | None = None
    aoi_path: str | None = None
    mask_out_path: str | None = None
    work_res: float = 0.1
    min_score: float = 3.0
    mass: bool = False
    use_osm: bool = True
    osm_file: str | None = None
    cache_dir: Path | None = None
    name: str = ""
    max_chips: int = 30
    max_candidates: int = 200
    after_date: str | None = None
    before_date: str | None = None
    sources: list[str] = field(default_factory=list)


@dataclass
class GraveSearchResult:
    config: GraveSearchConfig
    after: HRImage
    before: HRImage | None
    evidence: Evidence
    labels: np.ndarray
    candidates: list[GraveCandidate]
    mask: np.ndarray
    access: np.ndarray | None
    started: datetime
    finished: datetime | None = None
    notes: list[str] = field(default_factory=list)
    outputs: dict[str, Path] = field(default_factory=dict)


def _aoi(cfg: GraveSearchConfig):
    if not cfg.aoi_path:
        return None, None
    import geopandas as gpd

    gdf = gpd.read_file(cfg.aoi_path)
    if gdf.empty or gdf.crs is None:
        raise ValueError(f"{cfg.aoi_path} is empty or has no CRS.")
    return gdf, tuple(gdf.to_crs("EPSG:4326").total_bounds)


def _read_dsm(path: str, grid) -> np.ndarray:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(path) as src, WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                                               height=grid.height, resampling=Resampling.bilinear) as vrt:
        arr = vrt.read(1, masked=True).astype(np.float32)
    return np.ma.filled(arr, np.nan)


def search(cfg: GraveSearchConfig, progress=None, after: HRImage | None = None, before: HRImage | None = None) -> GraveSearchResult:
    """Run a grave search. Pass ``after``/``before`` directly to skip file loading (tests, evaluation)."""
    progress = progress or NullProgress()
    started = datetime.now(timezone.utc)
    t0 = time.monotonic()
    notes: list[str] = []

    progress.step("Loading imagery")
    gdf, bbox = _aoi(cfg)
    if after is None:
        grid = grid_for_raster(cfg.after_path, cfg.work_res, bbox)
        npx = grid.width * grid.height
        if npx > 60_000_000:
            raise ValueError(f"Search area is {grid.width} x {grid.height} pixels at {grid.res:.2f} m. "
                             "Raise --work-res or draw a tighter --aoi. A grave search is a walk, not a census.")
        after = read_to_grid(cfg.after_path, grid, cfg.bands, cfg.after_date)
    grid = after.grid
    if before is None and cfg.before_path:
        before = read_to_grid(cfg.before_path, grid, cfg.before_bands, cfg.before_date)
    if before is not None:
        progress.step("Aligning before and after")
        before = coregister(before, after)
        before = match_histograms(before, after)
        notes += before.notes

    mask = np.isfinite(after.data).all(axis=0)
    if before is not None:
        mask &= np.isfinite(before.data).all(axis=0)
    if gdf is not None:
        from rasterio.features import geometry_mask

        mask &= geometry_mask(gdf.to_crs(grid.crs).geometry, out_shape=grid.shape, transform=grid.transform, invert=True)
    if cfg.mask_out_path:
        import geopandas as gpd
        from rasterio.features import geometry_mask

        excl = gpd.read_file(cfg.mask_out_path)
        if excl.crs is None:
            raise ValueError(f"{cfg.mask_out_path} has no CRS.")
        if not excl.empty:
            out_mask = geometry_mask(excl.to_crs(grid.crs).geometry, out_shape=grid.shape, transform=grid.transform, invert=True)
            mask &= ~out_mask
            notes.append(f"{100 * out_mask.mean():.0f}% of the area masked out by {cfg.mask_out_path}")
    if not mask.any():
        raise ValueError("No usable pixels: the images and the search area do not overlap.")

    dsm = None
    if cfg.dsm_path:
        dsm = _read_dsm(cfg.dsm_path, grid)
        notes.append(f"surface model: {cfg.dsm_path}")

    access = None
    if cfg.use_osm:
        progress.step("Fetching roads, tracks, buildings and water from OpenStreetMap")
        try:
            from dgd.osm import built_mask, distance_to_ways, fetch_osm, load_osm_file

            if cfg.osm_file:
                osm = load_osm_file(cfg.osm_file, grid.lonlat_bbox())
                notes.append(f"OpenStreetMap read from local file {cfg.osm_file}")
            else:
                osm = fetch_osm(grid.lonlat_bbox(), cfg.cache_dir)
            access = distance_to_ways(grid, [coords for _, coords in osm["ways"]])
            built = built_mask(grid, osm)
            if built.any():
                mask &= ~built
                notes.append(f"{100 * built.mean():.0f}% of the area masked as mapped buildings, paved roads, car parks, plazas or water")
        except Exception as exc:
            notes.append(f"OpenStreetMap unavailable, access prior and building mask off: {exc}")
            log.warning("OpenStreetMap unavailable, ranking without the access prior: %s", exc)

    thermal = None
    if cfg.thermal_path:
        thermal = _read_dsm(cfg.thermal_path, grid)
        notes.append(f"thermal image: {cfg.thermal_path}")

    progress.step("Looking for grave-sized oddities")
    ev = gather_evidence(after, before, dsm, mask, thermal=thermal)

    progress.step("Measuring and ranking candidates")
    labels, cands = find_graves(ev, grid, cfg.min_score, cfg.mass, access)
    if len(cands) > cfg.max_candidates:
        notes.append(f"{len(cands)} candidates found, report keeps the top {cfg.max_candidates}")
        cands = cands[: cfg.max_candidates]
    log.info("Grave search: %d candidates in %.0fs", len(cands), time.monotonic() - t0)
    return GraveSearchResult(config=cfg, after=after, before=before, evidence=ev, labels=labels, candidates=cands,
                             mask=mask, access=access, started=started, finished=datetime.now(timezone.utc), notes=notes)


__all__ = ["GraveSearchConfig", "GraveSearchResult", "search"]
