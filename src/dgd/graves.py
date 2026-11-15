"""Search high resolution imagery for grave-sized ground disturbance.

What a fresh grave looks like from above, per the forensic literature and
common sense: a patch about 2 m by 1 m where the ground was opened. Soil
that should be under grass is on top of it. The vegetation is gone, or
trampled, or months later greener than its neighbors because something
underneath is feeding it. Often a low mound at first, sometimes a dip later
as the fill settles. The digging is the loud part. The body itself is quiet,
at least as far as cameras are concerned.

Three detectors, used when their inputs exist:

``anomaly``  one image only. Local Reed-Xiaoli (RX): how different is this
             grave-sized patch from the ring of ground around it, given how
             much that ring varies. Works without a "before" image, which in
             a real case usually does not exist.
``change``   before and after. The same RX, run on the color change between
             the two dates, so seasonal and lighting differences that hit the
             whole scene cancel and the local oddity stays.
``relief``   a surface model (DSM) from the drone survey. Mounds and dips of a
             few centimeters relative to the surrounding ground.

Then patches get measured, and the ones shaped like a person-sized hole move
up the list, as do the ones within walking distance of a road or track.

Honest expectations: the best published drone studies find about half of
simulated single graves with algorithms like these. This narrows a search.
It does not replace dogs, probes, ground radar or people walking in lines.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage

from dgd.highres import HRImage

log = logging.getLogger(__name__)

# Default size of a single clandestine grave, metres. Forensic reports put
# hand-dug graves around 1.5 to 2.5 m long and 0.5 to 1.2 m wide. A little
# slack on both ends, because people digging at night in a hurry do not use
# a tape measure.
SINGLE_LENGTH = (1.2, 3.2)
SINGLE_WIDTH = (0.4, 1.6)
MASS_LENGTH = (3.0, 400.0)
MASS_WIDTH = (1.5, 60.0)

MIN_PIXELS = 4

# Window sizes for local RX, metres. The target window averages a patch
# about grave width wide. The guard keeps the grave itself out of its own
# background. The outer ring is the "neighborhood" it is compared with.
TARGET_M = 0.6
GUARD_M = 4.0
OUTER_M = 16.0


def features(img: HRImage) -> dict[str, np.ndarray]:
    """Per-pixel features that separate soil from vegetation, from whatever bands exist.

    ``bright``  mean of the visible bands.
    ``soil``    (R - G) / (R + G). Soil, sand and dead grass up, green grass down.
    ``exg``     excess green, (2G - R - B) / (R + G + B), when there is blue.
    ``ndvi``    when there is NIR. Bare and disturbed soil show up best here
                while the vegetation has not grown back (Evers and Masters,
                Norton, Molina et al., via Koopman et al. 2025).
    ``ndre``    (NIR - RE) / (NIR + RE), when there is red edge too. Chlorophyll
                sensitive, the family of indices reported to catch graves at
                three to four months as the nitrogen comes up.
    """
    r, g = img.band("red"), img.band("green")
    vis = np.stack([img.band(k) for k in img.visible()])
    with np.errstate(invalid="ignore", divide="ignore"):
        f = {
            "bright": vis.mean(axis=0),
            "soil": np.where(r + g > 0, (r - g) / (r + g), np.nan),
        }
        if img.has("blue"):
            b = img.band("blue")
            s = r + g + b
            f["exg"] = np.where(s > 0, (2 * g - r - b) / s, np.nan)
        if img.has_nir:
            n = img.band("nir")
            f["ndvi"] = np.where(n + r > 0, (n - r) / (n + r), np.nan)
            if img.has("rededge"):
                re = img.band("rededge")
                f["ndre"] = np.where(n + re > 0, (n - re) / (n + re), np.nan)
    return {k: v.astype(np.float32) for k, v in f.items()}


def _robust_standardize(stack: np.ndarray) -> np.ndarray:
    """Median 0, robust sigma 1, per layer. Keeps one feature from shouting over the others."""
    out = np.empty_like(stack)
    for i, layer in enumerate(stack):
        ok = np.isfinite(layer)
        med = np.median(layer[ok]) if ok.any() else 0.0
        mad = np.median(np.abs(layer[ok] - med)) * 1.4826 if ok.any() else 1.0
        out[i] = (layer - med) / (mad if mad > 1e-9 else 1.0)
    return out


def local_rx(stack: np.ndarray, res: float, target_m: float = TARGET_M, guard_m: float = GUARD_M,
             outer_m: float = OUTER_M, block: int = 1024) -> np.ndarray:
    """Local Reed-Xiaoli anomaly score, as a Mahalanobis distance.

    For every pixel: average the features over a grave-width target window,
    estimate mean and covariance from the ring between the guard and outer
    windows, and measure how far the target sits from that ring in units of
    the ring's own spread. Box filters make it linear time. Processed in
    blocks with overlap so a 400 megapixel drone mosaic does not eat the RAM.
    """
    k, h, w = stack.shape
    t = max(1, int(round(target_m / res)))
    gi = max(t + 2, int(round(guard_m / res)) | 1)
    go = max(gi + 4, int(round(outer_m / res)) | 1)
    pad = go // 2 + 1
    out = np.full((h, w), np.nan, dtype=np.float32)
    valid_all = np.isfinite(stack).all(axis=0)
    filled_all = np.where(np.isfinite(stack), stack, 0.0).astype(np.float64)
    pairs = [(i, j) for i in range(k) for j in range(i, k)]
    for r0 in range(0, h, block):
        for c0 in range(0, w, block):
            rs = slice(max(0, r0 - pad), min(h, r0 + block + pad))
            cs = slice(max(0, c0 - pad), min(w, c0 + block + pad))
            x = filled_all[:, rs, cs]
            v = valid_all[rs, cs].astype(np.float64)
            n_o = ndimage.uniform_filter(v, go, mode="constant") * go * go
            n_i = ndimage.uniform_filter(v, gi, mode="constant") * gi * gi
            n_r = n_o - n_i
            s_r = [ndimage.uniform_filter(x[i] * v, go, mode="constant") * go * go
                   - ndimage.uniform_filter(x[i] * v, gi, mode="constant") * gi * gi for i in range(k)]
            mu = np.stack([s / np.maximum(n_r, 1) for s in s_r])
            cov = np.empty((x.shape[1], x.shape[2], k, k))
            for i, j in pairs:
                xx = x[i] * x[j] * v
                sxx = ndimage.uniform_filter(xx, go, mode="constant") * go * go - ndimage.uniform_filter(xx, gi, mode="constant") * gi * gi
                c = sxx / np.maximum(n_r, 1) - mu[i] * mu[j]
                cov[..., i, j] = c
                cov[..., j, i] = c
            # A dash of ridge so flat, featureless backgrounds do not divide by zero.
            cov += np.eye(k) * 1e-3
            tgt = np.stack([ndimage.uniform_filter(x[i] * v, t, mode="constant") / np.maximum(ndimage.uniform_filter(v, t, mode="constant"), 1e-9)
                            for i in range(k)])
            d = np.moveaxis(tgt - mu, 0, -1)[..., None]
            sol = np.linalg.solve(cov, d)
            d2 = (d * sol).sum(axis=(-2, -1))
            enough = (n_r > 0.5 * (go * go - gi * gi)) & valid_all[rs, cs]
            res_block = np.where(enough, np.sqrt(np.maximum(d2, 0)), np.nan).astype(np.float32)
            ro, co = r0 - rs.start, c0 - cs.start
            hh, ww = min(block, h - r0), min(block, w - c0)
            out[r0:r0 + hh, c0:c0 + ww] = res_block[ro:ro + hh, co:co + ww]
    return out


def to_z(score: np.ndarray, mask: np.ndarray | None = None) -> np.ndarray:
    """Robust z-score of an evidence map across the search area."""
    ok = np.isfinite(score) if mask is None else (np.isfinite(score) & mask)
    if not ok.any():
        return np.full_like(score, np.nan)
    med = np.median(score[ok])
    mad = np.median(np.abs(score[ok] - med)) * 1.4826
    return ((score - med) / (mad if mad > 1e-9 else 1.0)).astype(np.float32)


def dig_direction(after_f: dict, before_f: dict | None = None) -> np.ndarray:
    """Signed evidence that a patch went from plants to soil. Positive means more like a fresh dig."""
    def veg_key(*fs):
        for k in ("ndvi", "exg"):
            if all(k in f for f in fs):
                return k
        return None

    if before_f is None:
        key = veg_key(after_f)
        if key is None:
            return to_z(after_f["soil"])
        return (-to_z(after_f[key]) + to_z(after_f["soil"])) / math.sqrt(2)
    key = veg_key(after_f, before_f)
    if key is None:
        return to_z(after_f["soil"] - before_f["soil"])
    return (to_z(before_f[key] - after_f[key]) + to_z(after_f["soil"] - before_f["soil"])) / math.sqrt(2)


def relief(dsm: np.ndarray, res: float, scale_m: float = 3.0) -> np.ndarray:
    """Height above (or below) the local ground trend, metres.

    Gaussian trend at a few metres, subtracted. A 10 cm mound over a 2 m
    grave stands out. So does every molehill, bush and parked bicycle,
    which is why relief is one vote among several and not the judge.
    """
    filled = np.where(np.isfinite(dsm), dsm, np.nanmedian(dsm))
    trend = ndimage.gaussian_filter(filled, sigma=max(1.0, scale_m / res))
    out = (filled - trend).astype(np.float32)
    out[~np.isfinite(dsm)] = np.nan
    return out


def multiscale_rx(stack: np.ndarray, res: float, scales: tuple[float, ...], mask: np.ndarray | None) -> np.ndarray:
    """Local RX at several pixel sizes, each as a robust z, fused by taking the max.

    At 10 cm a plowed field is all clods and tractor lines, and that texture
    drowns a grave. At 25 cm the texture blurs away and the grave stands out.
    On grass it is the other way round. Running both and keeping the louder
    answer covers both, at the price of a second pass.
    """
    out = None
    for scale in scales:
        if scale <= 1.0:
            z = to_z(local_rx(stack, res), mask)
        else:
            sm = np.stack([ndimage.gaussian_filter(np.nan_to_num(b), scale / 2) for b in stack])
            small = np.stack([ndimage.zoom(b, 1 / scale, order=1) for b in sm])
            rx = local_rx(small, res * scale)
            up = ndimage.zoom(np.nan_to_num(rx, nan=np.nanmedian(rx)), (stack.shape[1] / rx.shape[0], stack.shape[2] / rx.shape[1]), order=1)
            up = up[: stack.shape[1], : stack.shape[2]]
            if up.shape != stack.shape[1:]:
                up = np.pad(up, ((0, stack.shape[1] - up.shape[0]), (0, stack.shape[2] - up.shape[1])), mode="edge")
            up[~np.isfinite(stack).all(axis=0)] = np.nan
            z = to_z(up, mask)
        out = z if out is None else np.fmax(out, z)
    return out


@dataclass
class Evidence:
    combined: np.ndarray
    layers: dict[str, np.ndarray] = field(default_factory=dict)
    dig: np.ndarray | None = None


def gather_evidence(after: HRImage, before: HRImage | None = None, dsm: np.ndarray | None = None,
                    mask: np.ndarray | None = None, scales: tuple[float, ...] = (1.0, 2.5),
                    thermal: np.ndarray | None = None) -> Evidence:
    """Run every detector the inputs allow and fuse them into one robust z map.

    Each layer is turned into a robust z over the search area. They are fused
    with Stouffer's method on the positive parts, so two detectors agreeing
    count for more than one shouting. The dig direction then gates the
    result: patches that went from soil to plants (new lawn, a tarp blown
    away) are pushed down, patches that went from plants to soil are kept.
    """
    res = after.grid.res
    fa = features(after)
    layers: dict[str, np.ndarray] = {}
    stack_a = _robust_standardize(np.stack(list(fa.values())))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        layers["anomaly"] = multiscale_rx(stack_a, res, scales, mask)
        fb = None
        if before is not None:
            fb = features(before)
            keys = [k for k in fa if k in fb]
            delta = np.stack([fa[k] - fb[k] for k in keys])
            layers["change"] = multiscale_rx(_robust_standardize(delta), res, scales, mask)
        if dsm is not None:
            rel = relief(dsm, res)
            layers["relief"] = to_z(np.abs(rel), mask)
        if thermal is not None:
            # Backfill runs a couple of degrees off its surroundings for the
            # first weeks. Hotter or colder both count, hence the RX distance.
            layers["thermal"] = multiscale_rx(_robust_standardize(thermal[None]), res, scales, mask)
        dig = dig_direction(fa, fb)
    pos = [np.clip(np.nan_to_num(v, nan=0.0), 0, None) for v in layers.values()]
    fused = (sum(pos) / math.sqrt(len(pos))).astype(np.float32)
    # Agreement with the dig direction scales the evidence between 0.25x and 1x.
    gate = np.clip(0.625 + 0.125 * np.nan_to_num(dig, nan=0.0), 0.25, 1.0)
    combined = (fused * gate).astype(np.float32)
    combined[~np.isfinite(layers["anomaly"])] = np.nan
    if mask is not None:
        combined[~mask] = np.nan
    return Evidence(combined=combined, layers=layers, dig=dig)


@dataclass
class GraveCandidate:
    label: int
    geometry: object  # shapely, grid CRS
    area_m2: float
    length_m: float
    width_m: float
    orientation_deg: float
    evidence_mean: float
    evidence_max: float
    shape_fit: float
    dig_mean: float
    layers: dict[str, float]
    lat: float
    lon: float
    rows: slice
    cols: slice
    access_m: float | None = None
    access_weight: float = 1.0
    rank_score: float = 0.0
    id: str = ""
    rank: int = 0

    @property
    def maps_url(self) -> str:
        return f"https://www.google.com/maps/search/?api=1&query={self.lat:.7f},{self.lon:.7f}"


def _range_fit(value: float, lo: float, hi: float, slack: float) -> float:
    """1 inside [lo, hi], falling off linearly to 0 at ``slack`` outside it."""
    if lo <= value <= hi:
        return 1.0
    gap = lo - value if value < lo else value - hi
    return max(0.0, 1.0 - gap / max(slack, 1e-6))


# Graves are long and narrow. Dung patches, molehills, bushes and dropped
# feed bags are roundish. Checked the hard way on Dutch pasture.
SINGLE_ASPECT = (1.4, 5.0)
MASS_ASPECT = (1.0, 50.0)


def shape_fit(length: float, width: float, res: float, length_range=SINGLE_LENGTH, width_range=SINGLE_WIDTH,
              aspect_range=SINGLE_ASPECT) -> float:
    """How grave-shaped is a patch, 0 to 1.

    Tolerance grows with pixel size. At 8 cm a grave has edges and a clear
    long axis. At 60 cm it is a smudge of six pixels, its "length" is a
    polite estimate, and its aspect ratio is mostly the pixel grid talking.
    """
    slack = max(0.5, 2.0 * res)
    aspect = length / max(width, 1e-6)
    aspect_slack = 0.3 + 3.0 * res
    return (_range_fit(length, *length_range, slack) * _range_fit(width, *width_range, slack)
            * _range_fit(aspect, *aspect_range, aspect_slack))


def access_weight(distance_m: float | None, scale_m: float = 100.0, floor: float = 0.3) -> float:
    """Ranking boost for patches near roads, tracks and paths.

    Carrying a body is hard work. Studies of solved cases put most burial
    sites within about 100 m of a path or track, often far closer to where
    the vehicle stopped. Far from any access still counts, just less.
    """
    if distance_m is None or not math.isfinite(distance_m):
        return 1.0
    return float(floor + (1 - floor) * math.exp(-distance_m / scale_m))


def find_graves(ev: Evidence, grid, min_score: float = 3.0, mass: bool = False,
                distance_to_access: np.ndarray | None = None) -> tuple[np.ndarray, list[GraveCandidate]]:
    """Threshold, group, measure, filter and rank grave-sized patches."""
    from pyproj import Transformer
    from rasterio.features import shapes
    from shapely.geometry import shape
    from shapely.ops import unary_union

    res = grid.res
    length_range, width_range = (MASS_LENGTH, MASS_WIDTH) if mass else (SINGLE_LENGTH, SINGLE_WIDTH)
    aspect_range = MASS_ASPECT if mass else SINGLE_ASPECT
    with np.errstate(invalid="ignore"):
        mask = ev.combined >= min_score
    # Close one-pixel gaps so a grave with a clump of grass on it stays one grave.
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3), bool), iterations=max(1, int(round(0.15 / res))))
    labels, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return np.zeros(grid.shape, dtype=np.int32), []
    area_px = np.bincount(labels.ravel(), minlength=n + 1)
    min_area = 0.5 * length_range[0] * width_range[0]
    max_area = 1.5 * length_range[1] * width_range[1]
    # And never fewer than 4 pixels. At 60 cm a grave covers about four, and a
    # one-pixel "grave" is a flower vase, a sprinkler or the sensor sneezing.
    keep = (area_px * res * res >= min_area) & (area_px * res * res <= max_area) & (area_px >= MIN_PIXELS)
    keep[0] = False
    remap = np.zeros(n + 1, dtype=np.int32)
    remap[keep] = np.arange(1, int(keep.sum()) + 1)
    labels = remap[labels]
    idx = np.arange(1, int(keep.sum()) + 1)
    if idx.size == 0:
        return labels, []

    boxes = ndimage.find_objects(labels)
    ev_mean = ndimage.mean(np.nan_to_num(ev.combined), labels, idx)
    ev_max = ndimage.maximum(np.nan_to_num(ev.combined), labels, idx)
    dig_mean = ndimage.mean(np.nan_to_num(ev.dig), labels, idx) if ev.dig is not None else np.zeros(len(idx))
    layer_means = {k: ndimage.mean(np.nan_to_num(v), labels, idx) for k, v in ev.layers.items()}
    acc = ndimage.minimum(distance_to_access, labels, idx) if distance_to_access is not None else None

    polys: dict[int, list] = {}
    for geom, value in shapes(labels, mask=labels > 0, transform=grid.transform, connectivity=8):
        polys.setdefault(int(value), []).append(shape(geom))
    to_ll = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)

    out = []
    for i, k in enumerate(idx):
        rows, cols = boxes[i]
        yy, xx = np.nonzero(labels[rows, cols] == k)
        yy = yy * res
        xx = xx * res
        cov = np.cov(np.stack([xx, yy])) if len(xx) > 1 else np.eye(2) * (res * res / 12)
        evals, evecs = np.linalg.eigh(cov + np.eye(2) * res * res / 12)
        # A uniform rectangle of length L has variance L^2 / 12 along its axis.
        length = math.sqrt(12 * evals[1])
        width = math.sqrt(12 * evals[0])
        angle = math.degrees(math.atan2(-evecs[1, 1], evecs[0, 1])) % 180
        geom = unary_union(polys[int(k)])
        pt = geom.centroid
        lon, lat = to_ll.transform(pt.x, pt.y)
        fit = shape_fit(length, width, res, length_range, width_range, aspect_range)
        dist = float(acc[i]) if acc is not None else None
        weight = access_weight(dist)
        cand = GraveCandidate(
            label=int(k), geometry=geom, area_m2=float(len(xx) * res * res), length_m=length, width_m=width,
            orientation_deg=angle, evidence_mean=float(ev_mean[i]), evidence_max=float(ev_max[i]), shape_fit=fit,
            dig_mean=float(dig_mean[i]), layers={k2: float(v[i]) for k2, v in layer_means.items()},
            lat=float(lat), lon=float(lon), rows=rows, cols=cols, access_m=dist, access_weight=weight,
        )
        # Past "clearly odd", how odd stops mattering much and shape takes over,
        # hence the logarithm. Shape keeps a floor: an odd shape is suspicious,
        # not disqualifying, because a grave dug at night is not a rectangle.
        cand.rank_score = math.log2(1 + max(cand.evidence_mean, 0.0)) * (0.3 + 0.7 * fit) * weight
        out.append(cand)
    out.sort(key=lambda c: c.rank_score, reverse=True)
    for r, c in enumerate(out, start=1):
        c.rank = r
        c.id = f"G{r:03d}"
    return labels, out


__all__ = [
    "Evidence",
    "GraveCandidate",
    "access_weight",
    "dig_direction",
    "features",
    "find_graves",
    "gather_evidence",
    "local_rx",
    "relief",
    "shape_fit",
    "to_z",
]
