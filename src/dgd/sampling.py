"""Training samples from labeled polygons.

Pixels under each polygon become rows of X, the polygon's class_id becomes y.
Optionally the polygon index comes back too, so validation can hold out whole
polygons instead of neighbors of pixels it trained on.
"""

from __future__ import annotations

import numpy as np
from rasterio.features import geometry_mask
from rasterio.transform import Affine
import geopandas as gpd


def extract_training_samples(
    feature_stack: np.ndarray,
    transform: Affine,
    training_gdf: gpd.GeoDataFrame,
    return_groups: bool = False,
):
    """Sample raster features under labeled polygons.

    Parameters
    ----------
    feature_stack : np.ndarray
        Feature cube shaped ``(n_features, rows, cols)``.
    transform : Affine
        Geo-transform matching ``feature_stack``.
    training_gdf : gpd.GeoDataFrame
        Polygon labels containing a ``class_id`` column.

    return_groups : bool
        Also return the polygon index of every sample.

    Returns
    -------
    tuple
        ``X`` of shape ``(n_samples, n_features)``, ``y`` labels, and the
        polygon index per sample when ``return_groups`` is set.

    Raises
    ------
    ValueError
        If no samples overlap the provided polygons.
    """

    rows, cols = feature_stack.shape[1:]
    x_parts = []
    y_parts = []
    g_parts = []

    for poly_idx, (_, rec) in enumerate(training_gdf.iterrows()):
        geom = [rec.geometry]
        class_id = int(rec["class_id"])

        pix_mask = geometry_mask(
            geom,
            transform=transform,
            invert=True,
            out_shape=(rows, cols),
        )

        samples = feature_stack[:, pix_mask].T
        valid = np.isfinite(samples).all(axis=1)
        samples = samples[valid]

        if len(samples):
            x_parts.append(samples)
            y_parts.append(np.full(len(samples), class_id, dtype=np.int32))
            g_parts.append(np.full(len(samples), poly_idx, dtype=np.int32))

    if not x_parts:
        raise ValueError("No training samples were extracted. Check geometry overlap and CRS.")

    x = np.vstack(x_parts)
    y = np.concatenate(y_parts)
    if return_groups:
        return x, y, np.concatenate(g_parts)
    return x, y


__all__ = ["extract_training_samples"]
