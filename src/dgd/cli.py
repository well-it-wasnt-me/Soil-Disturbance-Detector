"""Command-line interface for disturbed ground detection."""

from __future__ import annotations

import argparse


def parse_args():
    """Build and parse CLI arguments for the detector."""
    parser = argparse.ArgumentParser(
        description="Detect disturbed ground from before/after satellite imagery."
    )

    parser.add_argument("--before-path", default="before.tif", help="Path to before raster.")
    parser.add_argument("--after-path", default="after.tif", help="Path to after raster.")
    parser.add_argument(
        "--training-path",
        default="training_polygons.gpkg",
        help="Vector training data with a class_id column.",
    )
    parser.add_argument("--output-dir", default="outputs", help="Directory for outputs.")

    parser.add_argument(
        "--disturbed-class-id",
        type=int,
        default=3,
        help="Class ID used for disturbed ground in training polygons.",
    )
    parser.add_argument(
        "--min-patch-area-m2",
        type=float,
        default=400,
        help="Minimum disturbed patch area to keep in polygon output.",
    )
    parser.add_argument(
        "--prob-threshold",
        type=float,
        default=0.60,
        help="Probability threshold for disturbed binary mask.",
    )

    parser.add_argument(
        "--red-band-index",
        type=int,
        default=2,
        help="Zero-based red band index in the exported rasters.",
    )
    parser.add_argument(
        "--nir-band-index",
        type=int,
        default=3,
        help="Zero-based NIR band index in the exported rasters.",
    )

    # Download options for real-world example rasters.
    parser.add_argument(
        "--download-sentinel",
        action="store_true",
        help="Download before/after Sentinel-2 composites with geemap before running classification.",
    )
    parser.add_argument("--before-date", help="Before date in YYYY-MM-DD format.")
    parser.add_argument("--after-date", help="After date in YYYY-MM-DD format.")
    parser.add_argument("--lat", type=float, help="Latitude of AOI center.")
    parser.add_argument("--long", type=float, help="Longitude of AOI center.")
    parser.add_argument(
        "--buffer-m",
        type=float,
        default=1500,
        help="Half-size AOI buffer in meters around lat/long for Sentinel export.",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=60,
        help="Composite window length in days starting from each input date.",
    )
    parser.add_argument(
        "--cloud-pct",
        type=float,
        default=20,
        help="Maximum CLOUDY_PIXEL_PERCENTAGE for Sentinel-2 scenes.",
    )
    parser.add_argument(
        "--scale",
        type=float,
        default=10,
        help="Export scale in meters for Sentinel-2 rasters.",
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable console progress bar output.",
    )

    return parser.parse_args()


__all__ = ["parse_args"]
