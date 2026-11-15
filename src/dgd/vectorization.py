"""Raster to polygons, with a size filter. Specks under the threshold are noise until proven otherwise."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import shape


def raster_to_polygons(
    disturbed_binary: np.ndarray,
    transform: Affine,
    crs,
    min_patch_area_m2: float,
) -> gpd.GeoDataFrame:
    """Polygonize pixels equal to 1 and keep patches of at least ``min_patch_area_m2``."""
    geoms = []
    vals = []

    for geom, value in shapes(disturbed_binary.astype(np.uint8), mask=disturbed_binary == 1, transform=transform):
        if value == 1:
            geoms.append(shape(geom))
            vals.append(int(value))

    if not geoms:
        return gpd.GeoDataFrame({"value": []}, geometry=[], crs=crs)

    gdf = gpd.GeoDataFrame({"value": vals}, geometry=geoms, crs=crs)

    # Areas in square degrees are meaningless. Measure in a local UTM zone instead.
    if gdf.crs and gdf.crs.is_geographic:
        gdf_metric = gdf.to_crs(gdf.estimate_utm_crs())
        gdf["area_m2"] = gdf_metric.geometry.area.values
    else:
        gdf["area_m2"] = gdf.geometry.area

    gdf = gdf[gdf["area_m2"] >= min_patch_area_m2].copy()
    gdf.reset_index(drop=True, inplace=True)
    return gdf


__all__ = ["raster_to_polygons"]
