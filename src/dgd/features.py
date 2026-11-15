"""Spectral features and indices.

Nothing clever here. Ratios of bands that have been telling soil from grass
from ash since before anyone called it machine learning.
"""

from __future__ import annotations

import warnings

import numpy as np


def safe_divide(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Elementwise a / b, with NaN wherever the math would be nonsense.

    Nonsense means a non-finite input or a zero denominator. Returns float32.
    """

    out = np.full(np.shape(a), np.nan, dtype=np.float32)
    valid = np.isfinite(a) & np.isfinite(b) & (b != 0)
    out[valid] = a[valid] / b[valid]
    return out


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """(a - b) / (a + b), the workhorse of remote sensing since the 1970s."""

    return safe_divide(a - b, a + b)


def spectral_indices(bands: np.ndarray) -> dict[str, np.ndarray]:
    """Indices used by the dump scanner.

    Parameters
    ----------
    bands : np.ndarray
        Reflectance shaped ``(6, ...)`` in the order blue, green, red, nir,
        swir16, swir22 (see ``dgd.stac.REFLECTANCE_BANDS``).

    Returns
    -------
    dict
        ``ndvi`` vegetation. Drops when somebody scrapes a field bare.
        ``bsi`` bare soil index. Rises on exposed dirt and rubble.
        ``nbr`` normalized burn ratio. Drops when the pile gets set on fire,
        which in some places is the standard disposal method.
        ``bright`` mean reflectance of all six bands. Plastic and rubble are
        bright, ash is dark.
    """

    blue, green, red, nir, swir1, swir2 = (bands[i].astype(np.float32) for i in range(6))
    return {
        "ndvi": normalized_difference(nir, red),
        "bsi": normalized_difference(swir1 + red, nir + blue),
        "nbr": normalized_difference(nir, swir2),
        "bright": np.mean(np.stack([blue, green, red, nir, swir1, swir2]), axis=0).astype(np.float32),
    }


def build_feature_stack(before: np.ndarray, after: np.ndarray, red_band_index: int, nir_band_index: int) -> np.ndarray:
    """Per-pixel features from a before/after raster pair (classify mode).

    Parameters
    ----------
    before, after : np.ndarray
        Arrays shaped ``(bands, rows, cols)`` for the pre and post imagery.
    red_band_index, nir_band_index : int
        Zero-based band positions used for NDVI.

    Returns
    -------
    np.ndarray
        Stack shaped ``(n_features, rows, cols)``: both originals, their
        difference, NDVI before/after/delta and brightness before/after/delta.
    """

    ndvi_t1 = normalized_difference(before[nir_band_index], before[red_band_index])
    ndvi_t2 = normalized_difference(after[nir_band_index], after[red_band_index])
    with warnings.catch_warnings():
        # All-NaN pixels (nodata in every band) stay NaN. Numpy wants to talk about it. We do not.
        warnings.simplefilter("ignore", RuntimeWarning)
        brightness_t1 = np.nanmean(before, axis=0)
        brightness_t2 = np.nanmean(after, axis=0)

    return np.vstack([
        before,
        after,
        after - before,
        ndvi_t1[np.newaxis],
        ndvi_t2[np.newaxis],
        (ndvi_t2 - ndvi_t1)[np.newaxis],
        brightness_t1[np.newaxis],
        brightness_t2[np.newaxis],
        (brightness_t2 - brightness_t1)[np.newaxis],
    ])


__all__ = [
    "build_feature_stack",
    "normalized_difference",
    "safe_divide",
    "spectral_indices",
]
