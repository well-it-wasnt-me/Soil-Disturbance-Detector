"""Map overlays for the browser: small PNGs in Web Mercator, plus where they go.

The map UI shows the before and after imagery and the score of a finished
search on top of the satellite base map, with a swipe between the two dates.
Browsers do not read GeoTIFF, and a UTM raster laid on a Web Mercator map
without warping lands tens of metres off at the edges of a big box. So the
reports warp each layer to EPSG:3857 once, write a PNG no bigger than the
screen needs, and say in ``web/layers.json`` which corners it belongs to.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from dgd.aoi import Grid

log = logging.getLogger(__name__)

WEB_CRS = "EPSG:3857"
MAX_PX = 2048


def _target_transform(grid: Grid, max_px: int):
    from rasterio.warp import calculate_default_transform

    transform, width, height = calculate_default_transform(grid.crs, WEB_CRS, grid.width, grid.height, *grid.bounds)
    if max(width, height) > max_px:
        scale = max_px / max(width, height)
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        transform, width, height = calculate_default_transform(
            grid.crs, WEB_CRS, grid.width, grid.height, *grid.bounds, dst_width=width, dst_height=height)
    return transform, width, height


def _warp(stack: np.ndarray, grid: Grid, transform, width: int, height: int) -> np.ndarray:
    from rasterio.enums import Resampling
    from rasterio.warp import reproject

    stack = np.asarray(stack, dtype=np.float32)
    if stack.ndim == 2:
        stack = stack[None]
    shrink = width < grid.width * 0.75
    out = np.full((stack.shape[0], height, width), np.nan, dtype=np.float32)
    for i in range(stack.shape[0]):
        reproject(source=np.ascontiguousarray(stack[i]), destination=out[i], src_transform=grid.transform, src_crs=grid.crs,
                  dst_transform=transform, dst_crs=WEB_CRS, src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=Resampling.average if shrink else Resampling.bilinear)
    return out


def _rgb_png(rgb: np.ndarray, lo: np.ndarray, hi: np.ndarray, path: Path) -> None:
    from PIL import Image

    ok = np.isfinite(rgb).all(axis=0)
    scaled = (rgb - lo[:, None, None]) / (hi - lo + 1e-6)[:, None, None]
    img = np.clip(np.nan_to_num(scaled), 0, 1)
    rgba = np.zeros((rgb.shape[1], rgb.shape[2], 4), dtype=np.uint8)
    rgba[..., :3] = np.moveaxis(img * 255, 0, -1).astype(np.uint8)
    rgba[..., 3] = np.where(ok, 255, 0)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def _heat_png(score: np.ndarray, threshold: float, path: Path) -> None:
    """Magma, fully transparent half a unit below the threshold, opaque two units above it."""
    from matplotlib import colormaps
    from PIL import Image

    s = score[0]
    finite = np.isfinite(s)
    top = max(threshold + 4.0, float(np.nanpercentile(s[finite], 99.5)) if finite.any() else threshold + 4.0)
    norm = np.clip((np.nan_to_num(s, nan=0.0) - threshold) / (top - threshold), 0, 1)
    rgba = (colormaps["magma"](0.35 + 0.65 * norm) * 255).astype(np.uint8)
    alpha = np.clip((np.nan_to_num(s, nan=-1e9) - (threshold - 0.5)) / 2.5, 0, 1)
    rgba[..., 3] = (alpha * 230).astype(np.uint8)
    Image.fromarray(rgba, "RGBA").save(path, optimize=True)


def _stretch_for(*stacks: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    joined = np.concatenate([s.reshape(3, -1) for s in stacks if s is not None], axis=1)
    lo = np.array([np.nanpercentile(b, 2) if np.isfinite(b).any() else 0.0 for b in joined])
    hi = np.array([np.nanpercentile(b, 98) if np.isfinite(b).any() else 1.0 for b in joined])
    return lo, hi


def write_web_layers(
    out: Path,
    grid: Grid,
    images: dict[str, tuple[np.ndarray, str]],
    heat: dict[str, tuple[np.ndarray, float, str]],
    max_px: int = MAX_PX,
) -> list[Path]:
    """Write ``web/<name>.png`` for every layer and ``web/layers.json`` describing them.

    ``images`` maps a name to an RGB stack (3, rows, cols) in display order
    and a label. All images share one stretch, so before and after compare
    honestly. ``heat`` maps a name to a score raster, its threshold and a label.
    """
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds

    web = Path(out) / "web"
    web.mkdir(parents=True, exist_ok=True)
    transform, width, height = _target_transform(grid, max_px)
    west, south, east, north = transform_bounds(WEB_CRS, "EPSG:4326", *array_bounds(height, width, transform))
    warped = {name: _warp(arr, grid, transform, width, height) for name, (arr, _label) in images.items()}
    lo, hi = _stretch_for(*warped.values()) if warped else (None, None)
    layers, paths = [], []
    for name, (_arr, label) in images.items():
        path = web / f"{name}.png"
        _rgb_png(warped[name], lo, hi, path)
        layers.append({"id": name, "kind": "image", "label": label, "file": f"web/{name}.png"})
        paths.append(path)
    for name, (arr, threshold, label) in heat.items():
        path = web / f"{name}.png"
        _heat_png(_warp(arr, grid, transform, width, height), threshold, path)
        layers.append({"id": name, "kind": "heat", "label": label, "threshold": threshold, "file": f"web/{name}.png"})
        paths.append(path)
    meta = web / "layers.json"
    meta.write_text(json.dumps({"bounds": [[south, west], [north, east]], "size": [width, height], "layers": layers}, indent=1))
    return [*paths, meta]


def safe_write_web_layers(*args, **kwargs) -> list[Path]:
    """Same, but a failure costs the map overlays, never the report."""
    try:
        return write_web_layers(*args, **kwargs)
    except Exception as exc:  # a missing PIL codec should not sink a finished search
        log.warning("Map overlays not written: %s", exc)
        return []


__all__ = ["MAX_PX", "WEB_CRS", "safe_write_web_layers", "write_web_layers"]
