"""Turn a score map into a ranked list of candidate sites.

A pixel is a statistic. A candidate is a place somebody can drive to. This
module does the conversion and attaches everything an investigator needs to
decide whether the drive is worth the fuel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import numpy as np
from pyproj import Transformer
from rasterio.features import shapes
from scipy import ndimage
from shapely.geometry import shape
from shapely.ops import unary_union

from dgd.aoi import Grid
from dgd.anomaly import COMPONENTS
from dgd.landcover import CLASS_NAMES, majority_class

EIGHT_NEIGHBORS = np.ones((3, 3), dtype=bool)


@dataclass
class Candidate:
    """One lead. Geometry is in the grid CRS."""

    label: int
    geometry: object
    n_px: int
    area_m2: float
    score_mean: float
    score_max: float
    persistence: float
    signature: str
    kind: str
    components: dict[str, float]
    landcover: int
    lat: float
    lon: float
    rows: slice
    cols: slice
    field_like: bool = False
    context_weight: float = 1.0
    id: str = ""
    rank: int = 0
    rank_score: float = 0.0
    first_seen: date | None = None
    last_clear: date | None = None
    last_seen: date | None = None
    timeseries: list[tuple[date, float]] = field(default_factory=list)
    after_rgb: np.ndarray | None = None  # post-change composite for the image clip
    after_label: str = ""

    @property
    def landcover_name(self) -> str:
        return CLASS_NAMES.get(self.landcover, "unknown")

    @property
    def maps_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query={self.lat:.6f},{self.lon:.6f}"

    @property
    def osm_url(self) -> str:
        return f"https://www.openstreetmap.org/?mlat={self.lat:.6f}&mlon={self.lon:.6f}#map=18/{self.lat:.6f}/{self.lon:.6f}"


# A patch counts as "field-like" when it is at least this big and fills most
# of its minimum rotated rectangle. Farmers plow rectangles. Dumpers rarely
# bother with straight edges.
FIELD_MIN_AREA_M2 = 20000.0
FIELD_MIN_RECTANGULARITY = 0.6

# Ranking penalty for leads that look like ordinary farming: on cropland,
# or shaped like a whole field. They stay in the list, because burying waste
# in farmland is a time-honored tradition in some regions, but they stop
# crowding out the stranger stuff.
FARMING_WEIGHT = 0.5

# Transient events get half weight in the ranking. They are real leads, but
# "something was dug and then it went green again" also describes every
# pipeline repair, drainage ditch and septic tank in the county.
TRANSIENT_WEIGHT = 0.5


def rank_score(strength: float, persistence: float, n_px: int, context_weight: float = 1.0) -> float:
    """Ranking key. Strong, persistent and not microscopic goes to the top.

    Area enters through a logarithm so a 40 hectare construction site does
    not bury a strong 30 pixel dump by sheer acreage. ``context_weight``
    pushes down leads that look like routine farming.
    """
    return float(strength * persistence * math.log2(1 + n_px) * context_weight)


def rectangularity(geom) -> float:
    """Area of the shape over the area of its minimum rotated rectangle. 1.0 is a perfect box."""
    try:
        box = geom.minimum_rotated_rectangle
        return float(geom.area / box.area) if box.area > 0 else 0.0
    except Exception:
        return 0.0


def describe_signature(kind: str, material: float, burn: float, components: dict[str, float]) -> str:
    """What the spectra did, in plain words. Not what the object is.

    ``soil exposure``: the bare soil index rose more than brightness.
    ``brightening``: brightness rose more than the soil index.
    ``burn``: darkening plus a burn ratio drop beat both.

    These describe measurements on purpose. At 10 m a waste heap mixed with
    hedgerow and a freshly plowed field can trade places on any spectral
    label you care to invent. Checked the hard way: on the Kidlington test
    the dump read as soil and the plowed field read as bright. Calling them
    "waste" and "soil" would be lying with a straight face.
    """
    if kind == "transient":
        return "soil exposure"
    if burn > material:
        return "burn"
    if components.get("bright_gain", 0.0) > components.get("bare_gain", 0.0):
        return "brightening"
    return "soil exposure"


def find_candidates(
    grid: Grid,
    mask: np.ndarray,
    strength: np.ndarray,
    persistence: np.ndarray,
    material: np.ndarray,
    burn: np.ndarray,
    components: dict[str, np.ndarray],
    landcover: np.ndarray | None,
    kind: str = "persistent",
    min_area_m2: float = 300.0,
    label_offset: int = 0,
    merge_px: int = 2,
) -> tuple[np.ndarray, list[Candidate]]:
    """Group flagged pixels into connected patches and describe each one.

    Parameters
    ----------
    mask : np.ndarray
        Boolean pixels to group.
    strength : np.ndarray
        Per-pixel score that measures how strong the evidence is. The target
        median score for persistent sites, the event strength for transient.
    kind : str
        ``persistent`` (it showed up and stayed) or ``transient`` (it showed
        up and went away, the dig and cover pattern).
    label_offset : int
        Labels start at ``label_offset + 1`` so several kinds can share one
        label raster.
    merge_px : int
        Fragments closer than this many pixels become one site. One plowed
        field should be one lead, not seven, and nobody wants to drive to the
        same gate seven times.

    Returns
    -------
    tuple
        Label raster (0 = background) and unsorted candidates without ids.
        ``assign_ids`` ranks and names them once all kinds are collected.
    """
    mask = mask & np.isfinite(strength)
    if merge_px > 0:
        grown = ndimage.binary_dilation(mask, structure=EIGHT_NEIGHBORS, iterations=merge_px)
        labels, n = ndimage.label(grown, structure=EIGHT_NEIGHBORS)
        labels = np.where(mask, labels, 0)
    else:
        labels, n = ndimage.label(mask, structure=EIGHT_NEIGHBORS)
    if n == 0:
        return np.zeros(grid.shape, dtype=np.int32), []

    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    min_px = max(1, int(math.ceil(min_area_m2 / grid.pixel_area_m2)))
    keep = sizes >= min_px
    keep[0] = False
    remap = np.zeros(n + 1, dtype=np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1, dtype=np.int32)
    labels = remap[labels]
    n = int(keep.sum())
    if n == 0:
        return labels, []

    idx = np.arange(1, n + 1)
    with np.errstate(invalid="ignore"):
        strength_mean = ndimage.mean(strength, labels, idx)
        strength_max = ndimage.maximum(strength, labels, idx)
        pers_mean = ndimage.mean(np.nan_to_num(persistence), labels, idx)
        mat_mean = ndimage.mean(np.nan_to_num(material, nan=-np.inf), labels, idx)
        burn_mean = ndimage.mean(np.nan_to_num(burn, nan=-np.inf), labels, idx)
        comp_means = {k: ndimage.mean(np.nan_to_num(components[k]), labels, idx) for k in COMPONENTS}
    boxes = ndimage.find_objects(labels)

    polys: dict[int, list] = {}
    for geom, value in shapes(labels, mask=labels > 0, transform=grid.transform, connectivity=8):
        polys.setdefault(int(value), []).append(shape(geom))

    to_ll = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    sizes_kept = sizes[keep]
    out: list[Candidate] = []
    for i, k in enumerate(idx):
        geom = unary_union(polys[int(k)])
        pt = geom.centroid
        if not geom.contains(pt):
            pt = geom.representative_point()
        lon, lat = to_ll.transform(pt.x, pt.y)
        rows, cols = boxes[i]
        sub = labels[rows, cols] == k
        lc = majority_class(landcover[rows, cols][sub]) if landcover is not None else 0
        n_px = int(sizes_kept[i])
        pers = float(pers_mean[i]) if kind == "persistent" else TRANSIENT_WEIGHT
        comps = {c: float(comp_means[c][i]) for c in COMPONENTS}
        area = n_px * grid.pixel_area_m2
        field_like = area >= FIELD_MIN_AREA_M2 and rectangularity(geom) >= FIELD_MIN_RECTANGULARITY
        weight = FARMING_WEIGHT if (field_like or lc == 40) else 1.0
        out.append(
            Candidate(
                label=int(k) + label_offset,
                geometry=geom,
                n_px=n_px,
                area_m2=area,
                score_mean=float(strength_mean[i]),
                score_max=float(strength_max[i]),
                persistence=float(pers_mean[i]),
                signature=describe_signature(kind, float(mat_mean[i]), float(burn_mean[i]), comps),
                kind=kind,
                components=comps,
                landcover=lc,
                lat=float(lat),
                lon=float(lon),
                rows=rows,
                cols=cols,
                rank_score=rank_score(float(strength_mean[i]), pers, n_px, weight),
                field_like=field_like,
                context_weight=weight,
            )
        )
    labels = np.where(labels > 0, labels + label_offset, 0).astype(np.int32)
    return labels, out


ID_PREFIX = {"persistent": "P", "transient": "T"}


def assign_ids(cands: list[Candidate]) -> list[Candidate]:
    """Rank each kind separately and hand out ids P001, P002, T001 and so on.

    Persistent and transient leads are different animals. Ranking a pile of
    rubble against a plowed field on one scale is comparing apples to
    tractors, so each kind gets its own list. Persistent comes first because
    a pile that is still there is a better lead than a hole that healed.
    """
    out = []
    for kind in ("persistent", "transient"):
        group = sorted((c for c in cands if c.kind == kind), key=lambda c: c.rank_score, reverse=True)
        for rank, c in enumerate(group, start=1):
            c.rank = rank
            c.id = f"{ID_PREFIX[kind]}{rank:03d}"
        out += group
    return out


def chip_window(c: Candidate, shape: tuple[int, int], min_half: int = 32, pad: int = 16) -> tuple[slice, slice]:
    """Square-ish window around a candidate for image clips, clamped to the grid."""
    rc = (c.rows.start + c.rows.stop) // 2
    cc = (c.cols.start + c.cols.stop) // 2
    half = max(min_half, max(c.rows.stop - c.rows.start, c.cols.stop - c.cols.start) // 2 + pad)
    return slice(max(0, rc - half), min(shape[0], rc + half)), slice(max(0, cc - half), min(shape[1], cc + half))


def candidate_timeseries(
    cand: Candidate,
    labels: np.ndarray,
    scene_scores: np.ndarray,
    dates: Sequence[date],
    min_cover: float = 0.5,
) -> list[tuple[date, float]]:
    """Mean score inside the candidate for every pass, NaN if mostly cloudy."""
    sub = labels[cand.rows, cand.cols] == cand.label
    npx = int(sub.sum())
    series = []
    for t, d in enumerate(dates):
        vals = np.asarray(scene_scores[t, cand.rows, cand.cols], dtype=np.float32)[sub]
        good = np.isfinite(vals)
        if npx and good.sum() / npx >= min_cover:
            series.append((d, float(vals[good].mean())))
        else:
            series.append((d, float("nan")))
    return series


def appearance_window(
    series: Sequence[tuple[date, float]],
    target_start: date,
    min_score: float,
) -> tuple[date | None, date | None]:
    """When did it show up? Returns (last clear look before, first look with it).

    "First look with it" is the first target-window pass where the score
    crosses the threshold and stays above it in at least half of the later
    clear passes. One bright day does not make a dump. "Last clear look"
    is the most recent pass before that where the spot looked normal. The
    truth is somewhere in between, and that interval is what goes in the
    report, because pretending to know the exact day would be a lie.
    """
    vals = [(d, v) for d, v in series if math.isfinite(v)]
    first_idx = None
    for i, (d, v) in enumerate(vals):
        if d < target_start or v < min_score:
            continue
        later = [x for dd, x in vals[i:] if dd >= target_start]
        if later and float(np.median(later)) >= min_score:
            first_idx = i
            break
    if first_idx is None:
        return None, None
    last_clear = None
    for d, v in reversed(vals[:first_idx]):
        if v < min_score:
            last_clear = d
            break
    return last_clear, vals[first_idx][0]


def event_window(
    series: Sequence[tuple[date, float]],
    target_start: date,
    min_score: float,
) -> tuple[date | None, date | None, date | None]:
    """For transient sites: (last clear look before, first hot look, last hot look).

    Uses the longest run of consecutive clear passes at or above the
    threshold inside the target window. Ties go to the earliest run.
    """
    vals = [(d, v) for d, v in series if math.isfinite(v)]
    best: tuple[int, int] | None = None
    i = 0
    while i < len(vals):
        if vals[i][0] >= target_start and vals[i][1] >= min_score:
            j = i
            while j + 1 < len(vals) and vals[j + 1][1] >= min_score:
                j += 1
            if best is None or (j - i) > (best[1] - best[0]):
                best = (i, j)
            i = j + 1
        else:
            i += 1
    if best is None:
        return None, None, None
    last_clear = vals[best[0] - 1][0] if best[0] > 0 else None
    return last_clear, vals[best[0]][0], vals[best[1]][0]


__all__ = [
    "Candidate",
    "appearance_window",
    "assign_ids",
    "candidate_timeseries",
    "chip_window",
    "event_window",
    "find_candidates",
    "describe_signature",
    "rank_score",
    "rectangularity",
]
