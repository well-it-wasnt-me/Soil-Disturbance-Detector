"""Free Sentinel-2 L2A access through Element84 Earth Search.

No account, no API key, no quota manager. The imagery sits on public AWS
buckets as Cloud Optimized GeoTIFFs and GDAL pulls only the bytes it needs.

Two collections are merged. ESA Collection 1 (``sentinel-2-c1-l2a``) is the
consistent reprocessed archive and wins whenever it has the scene. The older
``sentinel-2-l2a`` fills the holes, and Collection 1 has a big one in 2022.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from dgd.aoi import Grid

log = logging.getLogger(__name__)

EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION_C1 = "sentinel-2-c1-l2a"
COLLECTION_LEGACY = "sentinel-2-l2a"

# Order matters. Downstream code indexes bands by position.
REFLECTANCE_BANDS = ("blue", "green", "red", "nir", "swir16", "swir22")

# Scene Classification Layer values worth keeping: 2 dark area, 4 vegetation,
# 5 bare soil, 6 water, 7 unclassified. Clouds, shadows, cirrus, snow and
# saturated pixels go in the bin. Class 2 stays because burned dump sites are
# dark, and tossing them would be throwing out the evidence with the shadows.
VALID_SCL = (2, 4, 5, 6, 7)

# Acquisitions with less clear sky than this over the grid are not worth the
# bandwidth. One percent of a 3 km box is still 900 pixels, plenty to sulk over.
MIN_VALID_FRACTION = 0.01

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.TIF,.tiff",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "GDAL_HTTP_MULTIRANGE": "YES",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "VSI_CACHE": "TRUE",
    "AWS_NO_SIGN_REQUEST": "YES",
}


@dataclass(frozen=True)
class Scene:
    """One Sentinel-2 granule (one MGRS tile on one pass)."""

    id: str
    collection: str
    datetime: datetime
    platform: str
    tile: str
    cloud_cover: float
    hrefs: Mapping[str, str]
    scale: float = 1e-4
    offset: float = 0.0


@dataclass(frozen=True)
class Acquisition:
    """Every granule from one satellite pass over the grid, mosaicked later."""

    platform: str
    date: date
    scenes: tuple[Scene, ...] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        return f"{self.platform}_{self.date.isoformat()}"

    @property
    def cloud_cover(self) -> float:
        return min(s.cloud_cover for s in self.scenes) if self.scenes else 100.0

    @property
    def scene_ids(self) -> list[str]:
        return [s.id for s in self.scenes]


def reflectance_offset(collection: str, properties: Mapping, raster_offset: float | None) -> float:
    """Additive offset turning scaled DN into surface reflectance.

    Collection 1 still carries the -1000 DN radiometric offset and says so in
    its metadata. The legacy collection either never had it (processing
    baseline < 04.00) or Element84 already subtracted it
    (``earthsearch:boa_offset_applied``). Both claims were checked against
    pixel values, because metadata lies about as often as witnesses do.
    """
    if collection == COLLECTION_LEGACY:
        if properties.get("earthsearch:boa_offset_applied"):
            return 0.0
        baseline = str(properties.get("s2:processing_baseline") or "00.00")
        try:
            major = float(baseline)
        except ValueError:
            major = 0.0
        return -0.1 if major >= 4.0 else 0.0
    return float(raster_offset) if raster_offset is not None else -0.1


def _scale_offset(item, collection: str) -> tuple[float, float]:
    asset = item.assets.get("red")
    bands = (asset.extra_fields.get("raster:bands") if asset is not None else None) or [{}]
    scale = bands[0].get("scale") or 1e-4
    return float(scale), reflectance_offset(collection, item.properties, bands[0].get("offset"))


def scene_from_item(item) -> Scene | None:
    """Build a Scene from a pystac Item, or None if bands are missing."""
    needed = (*REFLECTANCE_BANDS, "scl")
    if any(b not in item.assets for b in needed):
        return None
    collection = item.collection_id or ""
    scale, offset = _scale_offset(item, collection)
    props = item.properties
    tile = props.get("grid:code") or props.get("s2:mgrs_tile") or props.get("mgrs:grid_square") or ""
    return Scene(
        id=item.id,
        collection=collection,
        datetime=item.datetime,
        platform=str(props.get("platform", "sentinel-2")).lower(),
        tile=str(tile),
        cloud_cover=float(props.get("eo:cloud_cover", 100.0)),
        hrefs={b: item.assets[b].href for b in needed},
        scale=scale,
        offset=offset,
    )


def _legacy_sequence(scene_id: str) -> int:
    # Legacy ids end in _<seq>_L2A. Higher seq means reprocessed, so newer wins.
    parts = scene_id.split("_")
    try:
        return int(parts[-2])
    except (IndexError, ValueError):
        return 0


def group_acquisitions(scenes: Iterable[Scene]) -> list[Acquisition]:
    """Deduplicate granules and group them by satellite pass.

    Per (platform, day, tile) keep one granule: Collection 1 first, then the
    newest legacy reprocessing. Then bundle tiles of the same pass together.
    """
    best: dict[tuple[str, date, str], Scene] = {}
    for s in scenes:
        k = (s.platform, s.datetime.date(), s.tile)
        cur = best.get(k)
        if cur is None:
            best[k] = s
            continue
        rank_new = (s.collection == COLLECTION_C1, _legacy_sequence(s.id))
        rank_cur = (cur.collection == COLLECTION_C1, _legacy_sequence(cur.id))
        if rank_new > rank_cur:
            best[k] = s
    passes: dict[tuple[str, date], list[Scene]] = {}
    for (platform, day, _), s in best.items():
        passes.setdefault((platform, day), []).append(s)
    acqs = [
        Acquisition(platform=p, date=d, scenes=tuple(sorted(ss, key=lambda x: x.cloud_cover)))
        for (p, d), ss in passes.items()
    ]
    return sorted(acqs, key=lambda a: (a.date, a.platform))


def thin_acquisitions(acqs: Sequence[Acquisition], keep: int = 3, bin_days: int = 14) -> list[Acquisition]:
    """Keep the ``keep`` clearest passes in every ``bin_days`` stretch.

    Where orbits overlap and three satellites fly, a spot can get seven looks
    a fortnight. The statistics do not need seven, and every look costs about
    a dozen megabytes, because the archive stores imagery in 10 km blocks and
    a small area still has to fetch whole blocks. Three a fortnight is still
    a look every five days. ``keep`` of 0 or less keeps everything.
    """
    if keep <= 0 or not acqs:
        return list(acqs)
    origin = min(a.date for a in acqs)
    bins: dict[int, list[Acquisition]] = {}
    for a in acqs:
        bins.setdefault((a.date - origin).days // bin_days, []).append(a)
    kept = [a for group in bins.values() for a in sorted(group, key=lambda x: x.cloud_cover)[:keep]]
    return sorted(kept, key=lambda a: (a.date, a.platform))


def _read_band(href: str, grid: Grid, categorical: bool = False) -> np.ndarray:
    """Warp one band onto the grid, reading only the blocks it overlaps."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(href) as src:
        aligned = src.crs == grid.crs and abs(src.res[0] - grid.res) < 1e-6
        # Class codes and pixel-aligned bands get nearest. Everything else gets
        # bilinear, because nearest on a 20 m band upsampled to 10 m looks like
        # a bad 1980s video game.
        resampling = Resampling.nearest if (categorical or aligned) else Resampling.bilinear
        with WarpedVRT(
            src,
            crs=grid.crs,
            transform=grid.transform,
            width=grid.width,
            height=grid.height,
            resampling=resampling,
            src_nodata=0,
            nodata=0,
        ) as vrt:
            return vrt.read(1)


def read_acquisition(acq: Acquisition, grid: Grid) -> np.ndarray | None:
    """Read one pass as cloud-masked reflectance, shape (bands, rows, cols).

    Masked or missing pixels come back as NaN. Returns None when the sky was
    so bad over the grid that nothing usable remains.
    """
    import rasterio

    out = np.full((len(REFLECTANCE_BANDS), *grid.shape), np.nan, dtype=np.float32)
    with rasterio.Env(**GDAL_ENV):
        for scene in acq.scenes:
            need = np.isnan(out[0])
            if not need.any():
                break
            # SCL first. It is small, and a cloudy scene ends the conversation
            # before we waste six more downloads on it.
            scl = _read_band(scene.hrefs["scl"], grid, categorical=True)
            ok = need & np.isin(scl, VALID_SCL)
            if not ok.any():
                continue
            for i, band in enumerate(REFLECTANCE_BANDS):
                dn = _read_band(scene.hrefs[band], grid)
                good = ok & (dn > 0)
                out[i][good] = dn[good].astype(np.float32) * scene.scale + scene.offset
            # Any band missing means the pixel is no good for indices.
            incomplete = np.isnan(out).any(axis=0)
            out[:, incomplete] = np.nan
    if np.isfinite(out[0]).mean() < MIN_VALID_FRACTION:
        return None
    return out


class EarthSearchSource:
    """Sentinel-2 L2A from Element84 Earth Search, cached on disk.

    Parameters
    ----------
    cache_dir : Path or None
        Where per-pass arrays are kept. Re-running a scan with different
        thresholds then costs seconds instead of another download.
    max_cloud : float
        Granule-level cloud cover ceiling. Generous on purpose, because the
        pixel mask does the real work and a 70% cloudy granule can still be
        clear over a 3 km box.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        max_cloud: float = 70.0,
        workers: int = 8,
        url: str = EARTH_SEARCH_URL,
        collections: Sequence[str] = (COLLECTION_C1, COLLECTION_LEGACY),
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.max_cloud = max_cloud
        self.workers = max(1, workers)
        self.url = url
        self.collections = tuple(collections)
        self.failures: list[str] = []

    def describe(self) -> dict:
        return {"provider": "Element84 Earth Search", "url": self.url, "collections": list(self.collections), "max_cloud": self.max_cloud}

    def search(self, grid: Grid, start: date, end: date) -> list[Acquisition]:
        from pystac_client import Client

        client = Client.open(self.url)
        bbox = grid.lonlat_bbox()
        scenes: list[Scene] = []
        for coll in self.collections:
            search = client.search(
                collections=[coll],
                bbox=bbox,
                datetime=f"{start.isoformat()}/{end.isoformat()}",
                query={"eo:cloud_cover": {"lte": self.max_cloud}},
                max_items=None,
            )
            for item in search.items():
                s = scene_from_item(item)
                if s is not None:
                    scenes.append(s)
        acqs = group_acquisitions(scenes)
        log.info("Found %d passes (%d granules) between %s and %s", len(acqs), len(scenes), start, end)
        return acqs

    def _cache_paths(self, acq: Acquisition, grid: Grid) -> tuple[Path, Path] | None:
        if self.cache_dir is None:
            return None
        base = self.cache_dir / grid.key()
        return base / f"{acq.key}.npy", base / f"{acq.key}.json"

    def fetch(self, acq: Acquisition, grid: Grid) -> np.ndarray | None:
        paths = self._cache_paths(acq, grid)
        if paths is not None:
            arr_path, meta_path = paths
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                if meta.get("scene_ids") == acq.scene_ids:
                    if meta.get("empty"):
                        return None
                    if arr_path.exists():
                        return np.load(arr_path, mmap_mode="r")
        arr = read_acquisition(acq, grid)
        if paths is not None:
            arr_path, meta_path = paths
            arr_path.parent.mkdir(parents=True, exist_ok=True)
            if arr is not None:
                # float16 halves the disk bill. Sentinel-2 noise is bigger
                # than the rounding error, so nothing of value is lost.
                tmp = arr_path.with_name(arr_path.name + ".tmp.npy")
                np.save(tmp, arr.astype(np.float16))
                os.replace(tmp, arr_path)
            meta_path.write_text(json.dumps({"scene_ids": acq.scene_ids, "empty": arr is None}))
            if arr is not None:
                # Hand back the cached copy, not the float32 one in hand. A
                # first run then holds no more in RAM than a re-run, and both
                # score the exact same numbers.
                return np.load(arr_path, mmap_mode="r")
        return arr

    def fetch_all(
        self,
        acqs: Sequence[Acquisition],
        grid: Grid,
        on_done: Callable[[Acquisition], None] | None = None,
    ) -> dict[str, np.ndarray]:
        """Fetch passes in parallel. Returns {acq.key: array} for usable ones."""
        results: dict[str, np.ndarray] = {}
        self.failures = []
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self.fetch, a, grid): a for a in acqs}
            for fut in as_completed(futures):
                acq = futures[fut]
                try:
                    arr = fut.result()
                except Exception as exc:  # network gremlins, corrupt tiles, the usual
                    log.warning("Skipping %s: %s", acq.key, exc)
                    self.failures.append(acq.key)
                    arr = None
                if arr is not None:
                    results[acq.key] = arr
                if on_done:
                    on_done(acq)
        return results


__all__ = [
    "Acquisition",
    "EarthSearchSource",
    "REFLECTANCE_BANDS",
    "Scene",
    "group_acquisitions",
    "read_acquisition",
    "reflectance_offset",
    "scene_from_item",
    "thin_acquisitions",
]
