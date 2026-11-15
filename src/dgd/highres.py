"""High resolution imagery: load it, put it on a grid, line up two dates.

Drone orthomosaics, aerial survey photos and commercial satellite scenes all
show up in different projections, pixel sizes, band orders and bit depths.
This module beats them into one shape: float32 arrays on a shared metric
grid, with the bands labeled by what they are.

Two images of the same field taken a year apart never line up perfectly,
and never have the same colors. ``coregister`` fixes the first problem
(global shift, by phase correlation) and ``match_histograms`` the second.
Neither fixes a drone mosaic that is warped in the middle. Nothing fixes
that except flying it again properly.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

import numpy as np

from dgd.aoi import Grid, utm_epsg

log = logging.getLogger(__name__)

# Band orders people actually ship. "nrg" is color infrared: NIR in the red
# channel, red in green, green in blue. Dutch CIR aerials and many old
# survey products come like that. The multispectral drone presets follow the
# usual stacked-orthomosaic band order of each camera. Check yours. Any
# order can also be given as a comma list, like "green,red,rededge,nir".
BAND_SPECS = {
    "rgb": ("red", "green", "blue"),
    "bgr": ("blue", "green", "red"),
    "rgbn": ("red", "green", "blue", "nir"),
    "rgbi": ("red", "green", "blue", "nir"),
    "bgrn": ("blue", "green", "red", "nir"),
    "nrg": ("nir", "red", "green"),
    "sequoia": ("green", "red", "rededge", "nir"),
    "m3m": ("green", "red", "rededge", "nir"),
    "p4m": ("blue", "green", "red", "rededge", "nir"),
    "micasense": ("blue", "green", "red", "nir", "rededge"),
}
ROLES = ("blue", "green", "red", "rededge", "nir")


def band_roles(spec: str) -> tuple[str, ...]:
    """The role of each band in a band order, a preset name or a comma list. 'skip' and 'alpha' are allowed in lists."""
    key = spec.strip().lower()
    if key in BAND_SPECS:
        return BAND_SPECS[key]
    if "," in key:
        roles = tuple(r.strip() for r in key.split(","))
        bad = [r for r in roles if r and r not in ROLES and r not in ("skip", "alpha")]
        if bad:
            raise ValueError(f"Unknown band role(s) {bad}. Use {', '.join(ROLES)}, 'skip' or 'alpha'.")
        return roles
    raise ValueError(f"Unknown band order '{spec}'. Pick from: {', '.join(BAND_SPECS)}, or a comma list.")


def parse_bands(spec: str | None, count: int) -> dict[str, int]:
    """Map band roles to zero-based indices. Guess from the band count if no spec."""
    if spec:
        roles = band_roles(spec)
    elif count >= 4:
        roles = BAND_SPECS["rgbn"]
    elif count == 3:
        roles = BAND_SPECS["rgb"]
    else:
        raise ValueError(f"Need at least 3 bands, got {count}. A grayscale photo will not find a grave.")
    if len(roles) > count:
        raise ValueError(f"Band order '{spec}' needs {len(roles)} bands, the image has {count}.")
    out = {role: i for i, role in enumerate(roles) if role in ROLES}
    if not {"red", "green"} <= set(out):
        raise ValueError("Need at least a red and a green band.")
    return out


@dataclass
class HRImage:
    """One high resolution image on a working grid. ``data`` is (bands, rows, cols), NaN where missing."""

    data: np.ndarray
    grid: Grid
    roles: dict[str, int]
    source: str
    date: str | None = None
    notes: list[str] = field(default_factory=list)

    def band(self, role: str) -> np.ndarray:
        return self.data[self.roles[role]]

    @property
    def has_nir(self) -> bool:
        return "nir" in self.roles

    def has(self, role: str) -> bool:
        return role in self.roles

    def visible(self) -> list[str]:
        return [r for r in ("red", "green", "blue") if r in self.roles]

    def gray(self) -> np.ndarray:
        with np.errstate(all="ignore"):
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                return np.nanmean(self.data[[self.roles[r] for r in self.visible()]], axis=0)

    def display_bands(self) -> list[int]:
        """Bands for a picture a human can read: true color, or color infrared when there is no blue."""
        if "blue" in self.roles:
            return [self.roles["red"], self.roles["green"], self.roles["blue"]]
        if "nir" in self.roles:
            return [self.roles["nir"], self.roles["red"], self.roles["green"]]
        return [self.roles["red"], self.roles["green"], self.roles["green"]]


def grid_for_raster(path: str, work_res: float | None = None, bounds_lonlat: tuple | None = None) -> Grid:
    """Working grid in the raster's own projected CRS (or UTM if it is in degrees).

    ``work_res`` coarsens very fine imagery. A 2 m grave is 20 pixels long at
    10 cm, which is plenty. At 2 cm it is 100 pixels long and the computer
    spends its afternoon admiring individual grass blades.
    """
    import rasterio
    from pyproj import Transformer
    from rasterio.warp import transform_bounds

    with rasterio.open(path) as src:
        crs = src.crs
        if crs is None:
            raise ValueError(f"{path} has no CRS. Georeference it first, a raster with no address helps nobody.")
        left, bottom, right, top = src.bounds
        native = abs(src.res[0])
        if crs.is_geographic:
            lon, lat = (left + right) / 2, (bottom + top) / 2
            target = f"EPSG:{utm_epsg(lon, lat)}"
            left, bottom, right, top = transform_bounds(crs, target, left, bottom, right, top)
            native = native * 111320 * math.cos(math.radians(lat))
            crs_str = target
        else:
            crs_str = crs.to_string()
    if bounds_lonlat is not None:
        tr = Transformer.from_crs("EPSG:4326", crs_str, always_xy=True)
        xs, ys = tr.transform([bounds_lonlat[0], bounds_lonlat[2], bounds_lonlat[0], bounds_lonlat[2]],
                              [bounds_lonlat[1], bounds_lonlat[1], bounds_lonlat[3], bounds_lonlat[3]])
        left, right = max(left, min(xs)), min(right, max(xs))
        bottom, top = max(bottom, min(ys)), min(top, max(ys))
        if right <= left or top <= bottom:
            raise ValueError("The search area does not overlap the image.")
    res = max(native, work_res or 0.0)
    return grid_from_bounds(crs_str, left, bottom, right, top, res)


def grid_from_bounds(crs: str, left: float, bottom: float, right: float, top: float, res: float) -> Grid:
    from affine import Affine

    left = math.floor(left / res) * res
    top = math.ceil(top / res) * res
    width = max(1, int(round((right - left) / res)))
    height = max(1, int(round((top - bottom) / res)))
    return Grid(crs=crs, transform=Affine(res, 0.0, left, 0.0, -res, top), width=width, height=height)


def read_to_grid(path: str, grid: Grid, bands: str | None = None, date: str | None = None) -> HRImage:
    """Warp a raster onto the grid. Integer images are scaled to 0..1, alpha and nodata become NaN."""
    import rasterio
    from rasterio.enums import ColorInterp, Resampling
    from rasterio.vrt import WarpedVRT

    with rasterio.open(path) as src:
        interp = list(src.colorinterp)
        data_idx = []
        for i, ci in enumerate(interp):
            if ci == ColorInterp.alpha and _really_alpha(src, i + 1):
                continue
            if ci == ColorInterp.alpha:
                log.info("Band %d of %s is tagged alpha but holds real values. Treating it as data (NIR?).", i + 1, path)
            data_idx.append(i)
        spec_roles = band_roles(bands) if bands else ()
        if len(data_idx) < src.count and len(spec_roles) == src.count:
            # The band order describes every band in the file, alpha included,
            # so it indexes the file as is. The roles say which band is alpha.
            data_idx = list(range(src.count))
        # A band the order calls alpha masks pixels, whatever the file tags it.
        alpha_idx = [i for i, r in enumerate(spec_roles) if r == "alpha" and i < len(data_idx)]
        roles = parse_bands(bands, len(data_idx))
        downsample = grid.res > abs(src.res[0]) * 1.5 and not src.crs.is_geographic
        resampling = Resampling.average if downsample else Resampling.bilinear
        with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                       resampling=resampling) as vrt:
            arr = vrt.read([i + 1 for i in data_idx]).astype(np.float32)
            mask = vrt.dataset_mask() > 0
        for i in alpha_idx:
            mask &= arr[i] > 0
        if np.issubdtype(np.dtype(src.dtypes[0]), np.integer):
            arr /= float(np.iinfo(np.dtype(src.dtypes[0])).max)
    arr[:, ~mask] = np.nan
    if not np.isfinite(arr).any():
        raise ValueError(f"{path} has no valid pixels inside the search area.")
    return HRImage(data=arr, grid=grid, roles=roles, source=str(path), date=date)


def _really_alpha(src, band: int) -> bool:
    """A real alpha band holds 0 and 255 (or 0 and the dtype max) and nothing in between.

    GDAL happily tags the fourth band of any 4-band 8-bit image as alpha,
    including near infrared. Taking that at face value would quietly throw
    away the most useful band and use it as a transparency mask. So look.
    """
    sample = src.read(band, out_shape=(min(src.height, 512), min(src.width, 512)))
    vals = np.unique(sample)
    top = np.iinfo(sample.dtype).max if np.issubdtype(sample.dtype, np.integer) else 1.0
    return bool(np.all(np.isin(vals, [0, top])))


def phase_correlate(ref: np.ndarray, mov: np.ndarray) -> tuple[float, float]:
    """Shift (dy, dx) that moves ``mov`` onto ``ref``, to a tenth of a pixel or so.

    Classic phase correlation: the normalized cross-power spectrum of two
    shifted images is a pure phase ramp, and its inverse FFT peaks at the
    shift. A parabola through the peak and its neighbors gives subpixel.
    """
    a = np.nan_to_num(ref - np.nanmean(ref))
    b = np.nan_to_num(mov - np.nanmean(mov))
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    fa, fb = np.fft.fft2(a * win), np.fft.fft2(b * win)
    cross = fa * np.conj(fb)
    cross /= np.abs(cross) + 1e-12
    corr = np.real(np.fft.ifft2(cross))
    py, px = np.unravel_index(np.argmax(corr), corr.shape)

    def sub(c_m, c_0, c_p):
        den = c_m - 2 * c_0 + c_p
        return 0.0 if den == 0 else 0.5 * (c_m - c_p) / den

    h, w = corr.shape
    dy = py + sub(corr[(py - 1) % h, px], corr[py, px], corr[(py + 1) % h, px])
    dx = px + sub(corr[py, (px - 1) % w], corr[py, px], corr[py, (px + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dy), float(dx)


def coregister(before: HRImage, after: HRImage, max_shift_m: float = 5.0) -> HRImage:
    """Shift ``before`` onto ``after``. Refuses shifts bigger than ``max_shift_m``.

    A huge "shift" from phase correlation usually means it locked onto
    something silly, like a repeating crop row pattern. Better to stay
    put and say so than to slide the whole field sideways.
    """
    from scipy.ndimage import shift as nd_shift

    dy, dx = phase_correlate(after.gray(), before.gray())
    shift_m = math.hypot(dy, dx) * after.grid.res
    if shift_m > max_shift_m:
        before.notes.append(f"co-registration found a {shift_m:.1f} m shift and refused it (limit {max_shift_m} m)")
        log.warning("Co-registration wanted a %.1f m shift, more than the %.1f m limit. Left the images as they were.", shift_m, max_shift_m)
        return before
    filled = np.where(np.isfinite(before.data), before.data, np.nanmedian(before.data, axis=(1, 2))[:, None, None])
    moved = np.stack([nd_shift(b, (dy, dx), order=1, mode="nearest") for b in filled])
    valid = nd_shift(np.isfinite(before.data[0]).astype(np.float32), (dy, dx), order=0, mode="constant", cval=0) > 0.5
    moved[:, ~valid] = np.nan
    before.data = moved.astype(np.float32)
    before.notes.append(f"co-registered by ({dy:+.2f}, {dx:+.2f}) px = {shift_m:.2f} m")
    return before


def match_histograms(src: HRImage, ref: HRImage) -> HRImage:
    """Map each band of ``src`` so its distribution matches ``ref``.

    Different years, sun angles, cameras and seasons make the same grass a
    different green. Quantile mapping takes out the global part of that, so
    what remains is local, which is the part worth looking at.
    """
    out = src.data.copy()
    qs = np.linspace(0, 100, 201)
    for role, i in src.roles.items():
        if role not in ref.roles:
            continue
        j = ref.roles[role]
        s, r = src.data[i], ref.data[j]
        ok_s, ok_r = np.isfinite(s), np.isfinite(r)
        if ok_s.sum() < 100 or ok_r.sum() < 100:
            continue
        sq = np.percentile(s[ok_s], qs)
        rq = np.percentile(r[ok_r], qs)
        sq, idx = np.unique(sq, return_index=True)
        out[i][ok_s] = np.interp(s[ok_s], sq, rq[idx])
    src.data = out.astype(np.float32)
    src.notes.append("colors matched to the after image by per-band quantile mapping")
    return src


__all__ = [
    "BAND_SPECS",
    "ROLES",
    "HRImage",
    "coregister",
    "grid_for_raster",
    "grid_from_bounds",
    "match_histograms",
    "parse_bands",
    "phase_correlate",
    "read_to_grid",
]
