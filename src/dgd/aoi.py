"""Area of interest handling.

Every scan runs on a 10 m UTM grid. Sentinel-2 already lives on one, and
fighting it buys you nothing but resampling blur.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterator

import numpy as np
from affine import Affine
from pyproj import Transformer

PIXEL_M = 10.0
# Sentinel-2 tile origins sit on multiples of 60 m. Snap the grid there and the
# 10, 20 and 60 m bands line up without anybody having to interpolate.
SNAP_M = 60.0


@dataclass(frozen=True)
class Grid:
    """A north-up raster grid in a projected (metric) CRS."""

    crs: str
    transform: Affine
    width: int
    height: int

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def res(self) -> float:
        return float(self.transform.a)

    @property
    def pixel_area_m2(self) -> float:
        return float(abs(self.transform.a * self.transform.e))

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """(left, bottom, right, top) in grid CRS units."""
        left, top = self.transform.c, self.transform.f
        right = left + self.width * self.transform.a
        bottom = top + self.height * self.transform.e
        return (left, bottom, right, top)

    def lonlat_bbox(self) -> tuple[float, float, float, float]:
        """(min_lon, min_lat, max_lon, max_lat) covering the whole grid."""
        left, bottom, right, top = self.bounds
        xs = np.concatenate([np.linspace(left, right, 21), np.full(21, right), np.linspace(right, left, 21), np.full(21, left)])
        ys = np.concatenate([np.full(21, top), np.linspace(top, bottom, 21), np.full(21, bottom), np.linspace(bottom, top, 21)])
        lon, lat = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True).transform(xs, ys)
        return (float(np.min(lon)), float(np.min(lat)), float(np.max(lon)), float(np.max(lat)))

    def center_lonlat(self) -> tuple[float, float]:
        left, bottom, right, top = self.bounds
        lon, lat = Transformer.from_crs(self.crs, "EPSG:4326", always_xy=True).transform((left + right) / 2, (top + bottom) / 2)
        return (float(lon), float(lat))

    def key(self) -> str:
        """Short stable hash, used to key caches. Same grid, same key."""
        raw = f"{self.crs}|{tuple(round(v, 6) for v in self.transform[:6])}|{self.width}x{self.height}"
        return hashlib.sha1(raw.encode("ascii")).hexdigest()[:16]

    def blocks(self, size: int) -> Iterator[tuple[slice, slice]]:
        """Yield (row_slice, col_slice) tiles so big scans fit in memory."""
        for r0 in range(0, self.height, size):
            for c0 in range(0, self.width, size):
                yield slice(r0, min(r0 + size, self.height)), slice(c0, min(c0 + size, self.width))


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing a point."""
    zone = int(math.floor((lon + 180.0) / 6.0)) % 60 + 1
    return (32600 if lat >= 0 else 32700) + zone


def _snapped_grid(crs: str, left: float, bottom: float, right: float, top: float, res: float) -> Grid:
    left = math.floor(left / SNAP_M) * SNAP_M
    top = math.ceil(top / SNAP_M) * SNAP_M
    right = math.ceil(right / SNAP_M) * SNAP_M
    bottom = math.floor(bottom / SNAP_M) * SNAP_M
    width = int(round((right - left) / res))
    height = int(round((top - bottom) / res))
    if width <= 0 or height <= 0:
        raise ValueError("Area of interest collapsed to zero pixels. Check the coordinates.")
    return Grid(crs=crs, transform=Affine(res, 0.0, left, 0.0, -res, top), width=width, height=height)


def grid_from_point(lat: float, lon: float, radius_m: float, res: float = PIXEL_M) -> Grid:
    """Square grid centred on a point, extending radius_m in each direction."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError(f"Coordinates out of range: lat={lat}, lon={lon}. Latitude goes first.")
    if radius_m <= 0:
        raise ValueError("radius must be positive.")
    crs = f"EPSG:{utm_epsg(lon, lat)}"
    x, y = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lon, lat)
    return _snapped_grid(crs, x - radius_m, y - radius_m, x + radius_m, y + radius_m, res)


def grid_from_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float, res: float = PIXEL_M) -> Grid:
    """Grid covering a lon/lat bounding box, in the UTM zone of its centre."""
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError("Bounding box must be min_lon,min_lat,max_lon,max_lat with min < max.")
    clon, clat = (min_lon + max_lon) / 2, (min_lat + max_lat) / 2
    crs = f"EPSG:{utm_epsg(clon, clat)}"
    lons = np.concatenate([np.linspace(min_lon, max_lon, 21), np.full(21, max_lon), np.linspace(max_lon, min_lon, 21), np.full(21, min_lon)])
    lats = np.concatenate([np.full(21, max_lat), np.linspace(max_lat, min_lat, 21), np.full(21, min_lat), np.linspace(min_lat, max_lat, 21)])
    xs, ys = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(lons, lats)
    return _snapped_grid(crs, float(np.min(xs)), float(np.min(ys)), float(np.max(xs)), float(np.max(ys)), res)


def grid_from_vector(path: str, res: float = PIXEL_M) -> tuple[Grid, np.ndarray]:
    """Grid around a polygon file plus a boolean mask of pixels inside it."""
    import geopandas as gpd
    from rasterio.features import geometry_mask

    gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"No geometries in {path}.")
    if gdf.crs is None:
        raise ValueError(f"{path} has no CRS. Nobody can guess where on Earth that is, including me.")
    min_lon, min_lat, max_lon, max_lat = gdf.to_crs("EPSG:4326").total_bounds
    grid = grid_from_bbox(min_lon, min_lat, max_lon, max_lat, res)
    geoms = gdf.to_crs(grid.crs).geometry
    inside = geometry_mask(geoms, out_shape=grid.shape, transform=grid.transform, invert=True)
    return grid, inside


__all__ = ["Grid", "PIXEL_M", "grid_from_bbox", "grid_from_point", "grid_from_vector", "utm_epsg"]
