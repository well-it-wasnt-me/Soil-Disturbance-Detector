"""Optional Sentinel-2 download utilities."""

from __future__ import annotations


def download_sentinel_pair(args):
    """Export before/after Sentinel-2 composites using Earth Engine."""
    try:
        import ee
        import geemap
    except ImportError as exc:
        raise ImportError(
            "--download-sentinel requires geemap and earthengine-api. "
            "Install them with: pip install geemap earthengine-api"
        ) from exc

    if not all([args.before_date, args.after_date, args.lat is not None, args.long is not None]):
        raise ValueError(
            "--download-sentinel requires --before-date, --after-date, --lat, and --long."
        )

    from datetime import datetime, timedelta

    def window_end(date_text: str, days: int) -> str:
        dt = datetime.strptime(date_text, "%Y-%m-%d")
        return (dt + timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        ee.Initialize(project="1090295025291")
    except Exception:
        ee.Authenticate()
        ee.Initialize(project="1090295025291")

    point = ee.Geometry.Point([args.long, args.lat])
    aoi = point.buffer(args.buffer_m).bounds()

    def get_s2_composite(start_date: str, end_date: str):
        collection = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", args.cloud_pct))
            .select(["B2", "B3", "B4", "B8"])
        )

        count = collection.size().getInfo()
        if count == 0:
            raise ValueError(
                f"No Sentinel-2 scenes found for {start_date} to {end_date}. "
                "Try a wider window or a different date."
            )

        return collection.median().clip(aoi)

    before_end = window_end(args.before_date, args.window_days)
    after_end = window_end(args.after_date, args.window_days)

    print("Downloading Sentinel-2 before composite...")
    before_img = get_s2_composite(args.before_date, before_end)
    geemap.ee_export_image(
        before_img,
        filename=args.before_path,
        scale=args.scale,
        region=aoi,
        file_per_band=False,
    )

    print("Downloading Sentinel-2 after composite...")
    after_img = get_s2_composite(args.after_date, after_end)
    geemap.ee_export_image(
        after_img,
        filename=args.after_path,
        scale=args.scale,
        region=aoi,
        file_per_band=False,
    )

    print(f"Saved before raster to {args.before_path}")
    print(f"Saved after raster to {args.after_path}")


__all__ = ["download_sentinel_pair"]
