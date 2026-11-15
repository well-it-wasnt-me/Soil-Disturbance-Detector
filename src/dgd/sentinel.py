"""Optional Google Earth Engine backend for before/after composites.

The free STAC path in ``dgd.composite`` is the default and needs no account.
This exists for people who already live in Earth Engine. Bring your own
project id. Nobody else's credentials are baked in here anymore.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping

from dgd.aoi import Grid

EE_BANDS = ["B2", "B3", "B4", "B8", "B11", "B12"]
# SCL classes kept, same as the STAC path: dark area, vegetation, bare, water, unclassified.
EE_VALID_SCL = [2, 4, 5, 6, 7]
# Earth Engine exports masked pixels as 0, which is a perfectly plausible
# reflectance. Fill them with this instead, then turn it into NaN on disk.
EE_FILL = -9999.0


def _nan_nodata(path: Path) -> None:
    """Rewrite an export so masked pixels are NaN and tagged as nodata, like the STAC composites."""
    import numpy as np
    import rasterio

    from dgd.composite import BAND_LABELS

    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        profile = {"driver": "GTiff", "height": src.height, "width": src.width, "count": src.count, "dtype": "float32",
                   "crs": src.crs, "transform": src.transform, "nodata": float("nan"), "compress": "deflate"}
        if src.nodata is not None and np.isfinite(src.nodata):
            arr[arr == src.nodata] = np.nan
    arr[arr == EE_FILL] = np.nan
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        if arr.shape[0] == len(BAND_LABELS):
            for i, label in enumerate(BAND_LABELS, start=1):
                dst.set_band_description(i, label)


def _import_ee():
    try:
        import ee
        import geemap
    except ImportError as exc:
        raise ImportError(
            "--source ee needs geemap and earthengine-api. Install them with: pip install 'dgd[ee]'"
        ) from exc
    return ee, geemap


def download_sentinel_pair_ee(
    grid: Grid,
    windows: Mapping[str, tuple[date, date]],
    paths: Mapping[str, Path],
    project: str | None,
    max_cloud: float = 70,
) -> dict[str, Path]:
    """Export cloud-masked median composites through Earth Engine.

    Band order matches the STAC path: blue, green, red, nir, swir16, swir22,
    as reflectance (0 to 1).
    """
    if not project:
        raise ValueError("Earth Engine needs a project id. Pass --ee-project or set EE_PROJECT.")
    ee, geemap = _import_ee()
    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)

    min_lon, min_lat, max_lon, max_lat = grid.lonlat_bbox()
    aoi = ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])

    def mask_scl(img):
        scl = img.select("SCL")
        ok = scl.eq(EE_VALID_SCL[0])
        for v in EE_VALID_SCL[1:]:
            ok = ok.Or(scl.eq(v))
        return img.updateMask(ok)

    out = {}
    for name, (start, end) in windows.items():
        coll = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(aoi)
            .filterDate(start.isoformat(), end.isoformat())
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
        )
        if coll.size().getInfo() == 0:
            raise RuntimeError(f"No Sentinel-2 scenes for {start} to {end}. Widen the window.")
        img = coll.map(mask_scl).select(EE_BANDS).median().divide(10000).toFloat().clip(aoi).unmask(EE_FILL)
        path = Path(paths[name])
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.stat().st_mtime_ns if path.exists() else None
        geemap.ee_export_image(img, filename=str(path), scale=grid.res, region=aoi, crs=grid.crs, file_per_band=False)
        # geemap prints export errors instead of raising them.
        if not path.exists() or path.stat().st_mtime_ns == before:
            raise RuntimeError(f"Earth Engine export of the {name} composite failed. See the messages above.")
        _nan_nodata(path)
        out[name] = path
    return out


__all__ = ["EE_BANDS", "download_sentinel_pair_ee"]
