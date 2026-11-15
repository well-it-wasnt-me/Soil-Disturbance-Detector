"""Overlay PNG for classify mode: the before image, the detections, a red box and a coordinate."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import rasterio
from matplotlib.patches import Rectangle


def _stretch_to_rgb(arr: np.ndarray, red_idx: int, green_idx: int, blue_idx: int) -> np.ndarray:
    bands = []
    for idx in [red_idx, green_idx, blue_idx]:
        band = arr[idx]
        p2, p98 = np.nanpercentile(band, [2, 98])
        scaled = (band - p2) / (p98 - p2 + 1e-6)
        scaled = np.clip(scaled, 0, 1)
        bands.append(scaled)
    rgb = np.dstack(bands)
    return rgb


def _get_extent(bounds) -> list:
    return [bounds.left, bounds.right, bounds.bottom, bounds.top]


def save_overlay(
    before_path: str,
    disturbed_gdf: gpd.GeoDataFrame,
    overlay_path: Path,
    red_band_index: int,
):
    """Render before-image RGB and overlay disturbed polygons + bounding box."""
    with rasterio.open(before_path) as src:
        arr = src.read().astype(np.float32)
        bounds = src.bounds
        crs = src.crs

    if arr.shape[0] >= 3:
        red = red_band_index
        green = max(red_band_index - 1, 0)
        blue = max(red_band_index - 2, 0)
        rgb = _stretch_to_rgb(arr, red, green, blue)
    else:
        band = arr[0]
        p2, p98 = np.nanpercentile(band, [2, 98])
        scaled = np.clip((band - p2) / (p98 - p2 + 1e-6), 0, 1)
        rgb = np.dstack([scaled, scaled, scaled])

    extent = _get_extent(bounds)

    fig, ax = plt.subplots(figsize=(8, 8))
    ax.imshow(rgb, extent=extent)

    # Disturbed polygons outline
    disturbed_gdf.boundary.plot(ax=ax, color="yellow", linewidth=1.5)

    # Red rectangle around all disturbed areas
    minx, miny, maxx, maxy = disturbed_gdf.total_bounds
    rect = Rectangle(
        (minx, miny),
        maxx - minx,
        maxy - miny,
        linewidth=2,
        edgecolor="red",
        facecolor="none",
    )
    ax.add_patch(rect)

    # Coordinates of the centroid, if the projection cooperates
    label = None
    try:
        center_proj_x = (minx + maxx) / 2
        center_proj_y = miny + 0.02 * (maxy - miny)
        centroid_ll = disturbed_gdf.to_crs("EPSG:4326").geometry.union_all().centroid
        label = f"Lat {centroid_ll.y:.5f}, Lon {centroid_ll.x:.5f}"
        ax.text(
            center_proj_x,
            center_proj_y,
            label,
            color="red",
            fontsize=10,
            ha="center",
            va="bottom",
            bbox=dict(facecolor="white", alpha=0.7, edgecolor="none"),
            clip_on=True,
        )
    except Exception:
        label = None

    ax.set_title("Detected Disturbance")
    ax.set_xlabel(f"X ({crs})")
    ax.set_ylabel(f"Y ({crs})")
    plt.tight_layout()
    fig.savefig(overlay_path, dpi=200)
    plt.close(fig)
    print(f"Saved overlay to {overlay_path}" + (f" with centroid {label}" if label else ""))


__all__ = ["save_overlay"]
