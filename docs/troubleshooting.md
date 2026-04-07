# Troubleshooting

## Missing training file
Error: `pyogrio.errors.DataSourceError: training_polygons.gpkg: No such file or directory`  
Fix: generate a placeholder file  
```bash
python create_training_polygons.py --raster before.tif --output training_polygons.gpkg --disturbed-class-id 3
```
Then rerun `main.py` with `--training-path training_polygons.gpkg`.

## CRS or overlap issues
- Ensure polygons overlap the raster extent.  
- Polygons are reprojected to raster CRS automatically, but if nothing is extracted, verify in QGIS that geometries sit over the imagery.

## No disturbed polygons detected
- Lower `--prob-threshold` (e.g., 0.5 → 0.4).  
- Lower `--min-patch-area-m2` to keep smaller patches.  
- Confirm your `disturbed-class-id` matches label values.

## Sentinel download failures
- Make sure Earth Engine is authenticated (`ee.Authenticate()` + `ee.Initialize()` once).  
- Widen `--window-days` or relax `--cloud-pct` if no scenes are found.  
- Check network access if exports hang.

## Overlay not created
If `disturbed_overlay.png` is missing, the log will show the exception. Common causes: no disturbed polygons, missing Matplotlib backend in headless setups (use the default Agg backend), or insufficient disk space.
