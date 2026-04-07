"""Feature engineering utilities for disturbed-ground detection."""

from __future__ import annotations

import numpy as np


def safe_divide(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Divide arrays elementwise while guarding against invalid math.

    Parameters
    ----------
    a, b : np.ndarray
        Arrays of identical shape to divide.

    Returns
    -------
    np.ndarray
        Division result where valid positions are finite and denominator is
        non-zero; invalid positions are set to ``np.nan``.
    """

    out = np.zeros_like(a, dtype=np.float32)
    valid = np.isfinite(a) & np.isfinite(b) & (b != 0)
    out[valid] = a[valid] / b[valid]
    out[~valid] = np.nan
    return out


def normalized_difference(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute the normalized difference index for two bands.

    Uses ``(a - b) / (a + b)`` with safe division semantics.
    """

    return safe_divide(a - b, a + b)


def build_feature_stack(before: np.ndarray, after: np.ndarray, red_band_index: int, nir_band_index: int) -> np.ndarray:
    """Assemble per-pixel features from before/after rasters.

    Parameters
    ----------
    before, after : np.ndarray
        Arrays with shape ``(bands, rows, cols)`` for pre- and post-event imagery.
    red_band_index, nir_band_index : int
        Zero-based indices for red and NIR bands used in NDVI computation.

    Returns
    -------
    np.ndarray
        Feature stack containing original bands, deltas, NDVI, and brightness
        components with shape ``(n_features, rows, cols)``.
    """

    red_t1 = before[red_band_index]
    nir_t1 = before[nir_band_index]
    red_t2 = after[red_band_index]
    nir_t2 = after[nir_band_index]

    ndvi_t1 = normalized_difference(nir_t1, red_t1)
    ndvi_t2 = normalized_difference(nir_t2, red_t2)
    ndvi_diff = ndvi_t2 - ndvi_t1

    brightness_t1 = np.nanmean(before, axis=0)
    brightness_t2 = np.nanmean(after, axis=0)
    brightness_diff = brightness_t2 - brightness_t1

    band_delta = after - before

    features = np.vstack([
        before,
        after,
        band_delta,
        ndvi_t1[np.newaxis, :, :],
        ndvi_t2[np.newaxis, :, :],
        ndvi_diff[np.newaxis, :, :],
        brightness_t1[np.newaxis, :, :],
        brightness_t2[np.newaxis, :, :],
        brightness_diff[np.newaxis, :, :],
    ])
    return features


__all__ = [
    "build_feature_stack",
    "normalized_difference",
    "safe_divide",
]
