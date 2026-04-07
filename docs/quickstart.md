# Quickstart

## 1. Install dependencies
```bash
$ python -m venv .venv
$ source .venv/bin/activate
$ pip install -r requirements.txt
```

## 2. (Optional) Earth Engine authentication
Needed only when using `--download-sentinel`:
```python
import ee
ee.Authenticate(project="YOUR-PROJ-ID")
ee.Initialize()
print("EE ready")
```

## 3. Create placeholder labels (if you have none)
```bash
$ python create_training_polygons.py \
--raster before.tif \
--output training_polygons.gpkg \
--disturbed-class-id 3
```
Open `training_polygons.gpkg` in QGIS to redraw/edit real polygons later.
You can also auto-generate a properly structured GPKG with class IDs using the helper above; it reads raster bounds/CRS and creates valid polygons, so the file is ready for editing or direct use.

Requirements for real labels:

- Keep a `class_id` field (disturbed class should match `--disturbed-class-id`, default 3).
- CRS must match the rasters; if not, reproject in QGIS before saving.
- Editing in QGIS (brief):
  - Add `before.tif` as a basemap; ensure the project CRS matches the raster.
  - Add or create a polygon layer stored as `GPKG` with a `class_id` integer field.
  - Enable editing, draw polygons for disturbed areas (`class_id` = 3) and background (`class_id` = 0 or other).
  - Save edits, stop editing, and verify the layer CRS still matches the raster before exporting.

## 4. Run end-to-end (download + analysis)
```bash
$ python main.py \
  --download-sentinel \
  --before-date 2025-06-01 \
  --after-date 2025-12-01 \
  --lat 53.3498 \
  --long -6.2603 \
  --buffer-m 1500 \
  --window-days 60 \
  --cloud-pct 20 \
  --scale 10 \
  --training-path training_polygons.gpkg \
  --disturbed-class-id 3 \
  --prob-threshold 0.6 \
  --min-patch-area-m2 400 \
  --output-dir outputs/dublin_demo
```

## 5. Run with existing rasters
```bash
$ python main.py \
  --before-path data/before.tif \
  --after-path data/after.tif \
  --training-path training_polygons.gpkg \
  --output-dir outputs/site1
```

## 6. Inspect outputs
- `disturbed_overlay.png` for a quick visual (red box, yellow outlines, centroid GPS).
- `disturbed_polygons.gpkg` for vector results.
- `metrics.txt` for validation stats.

## 7. Run tests
```bash
$ pytest
```
The suite is designed for high coverage and uses tiny synthetic rasters, so it completes quickly.

## 8. Next steps
- Iterate on labels and thresholds; rerun the CLI as needed.
