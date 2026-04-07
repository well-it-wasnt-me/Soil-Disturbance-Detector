"""
Quick helper to create a placeholder training_polygons.gpkg so the pipeline runs
without manual labeling. Polygons are simple boxes placed relative to the raster
extent; edit or redraw later in QGIS for real labels.
"""

import argparse
from pathlib import Path

import geopandas as gpd
import rasterio
from shapely.geometry import box


def parse_args():
    parser = argparse.ArgumentParser(description="Create dummy training polygons.")
    parser.add_argument(
        "--raster",
        default="before.tif",
        help="Raster used only to grab bounds/CRS (e.g., before.tif).",
    )
    parser.add_argument(
        "--output",
        default="training_polygons.gpkg",
        help="Output GeoPackage path.",
    )
    parser.add_argument(
        "--disturbed-class-id",
        type=int,
        default=3,
        help="Class id to use for the disturbed sample polygon.",
    )
    return parser.parse_args()


def make_box(bounds, rel_x, rel_y, rel_w, rel_h):
    """Build a shapely box using fractions of the raster width/height."""
    width = bounds.right - bounds.left
    height = bounds.top - bounds.bottom
    minx = bounds.left + rel_x * width
    maxx = minx + rel_w * width
    miny = bounds.bottom + rel_y * height
    maxy = miny + rel_h * height
    return box(minx, miny, maxx, maxy)


def main():
    args = parse_args()
    raster_path = Path(args.raster)
    if not raster_path.exists():
        raise FileNotFoundError(f"Raster not found: {raster_path}")

    with rasterio.open(raster_path) as src:
        bounds = src.bounds
        crs = src.crs

    # Three example boxes: one disturbed, two background.
    geometries = [
        make_box(bounds, 0.35, 0.35, 0.1, 0.1),  # disturbed sample
        make_box(bounds, 0.15, 0.15, 0.12, 0.12),  # background
        make_box(bounds, 0.65, 0.65, 0.12, 0.12),  # background
    ]
    class_ids = [
        args.disturbed_class_id,
        0,
        0,
    ]

    gdf = gpd.GeoDataFrame({"class_id": class_ids}, geometry=geometries, crs=crs)
    gdf.to_file(args.output, driver="GPKG")
    print(f"Wrote dummy training polygons to {args.output}")
    print("Open this file in QGIS and redraw/edit polygons for real labels.")


if __name__ == "__main__":
    main()
