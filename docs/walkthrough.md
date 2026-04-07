# End-to-End Walkthrough

This guide shows every step, from environment setup to inspecting outputs, without skipping anything.

## 1. Create and activate a virtual environment
```bash
python -m venv .venv
source .venv/bin/activate
```

## 2. Install dependencies
```bash
pip install -r requirements.txt
```

## 3. Authenticate Earth Engine (only if you will download Sentinel)
```bash
python - <<'PY'
import ee
ee.Authenticate()
ee.Initialize()
print("EE ready")
PY
```

## 4. Get rasters
- **Option A (auto-download)**: use `--download-sentinel` with dates, lat/long, buffer, cloud filter, and scale. This writes `before.tif` and `after.tif`.
- **Option B (existing rasters)**: place your own `before.tif` / `after.tif` in the working directory or pass their paths.

## 5. Create training polygons
Polygons must have a `class_id` column; the disturbed class is `--disturbed-class-id` (default 3).

- **Fast placeholder** (dummy boxes you can edit later):
  ```bash
  python create_training_polygons.py --raster before.tif --output training_polygons.gpkg --disturbed-class-id 3
  ```
  This automatically builds a valid GPKG with `class_id` and matching CRS based on the raster, so it can be used immediately or opened in QGIS for refinement.
- **Edit real labels in QGIS**:
  1. Open `before.tif` as a base layer.
  2. Load `training_polygons.gpkg` (or create a new polygon layer) and ensure the CRS matches the raster.
  3. Add polygons marking disturbed areas with `class_id = 3` and background with another class (e.g., 0).
  4. Save the layer to `training_polygons.gpkg`.
  5. Double-check the saved layer’s CRS is unchanged and overlaps the raster.

## 6. Run the detector
```bash
python main.py \
  --before-path before.tif \
  --after-path after.tif \
  --training-path training_polygons.gpkg \
  --output-dir outputs/demo \
  --disturbed-class-id 3 \
  --prob-threshold 0.6 \
  --min-patch-area-m2 400
```
Add `--download-sentinel` and its arguments if you want auto-download (see Quickstart).

## 7. Review outputs (in `outputs/demo`)
- `metrics.txt` — classification report and confusion matrix.
- `disturbed_probability.tif` — continuous probabilities.
- `disturbed_binary.tif` — thresholded mask.
- `class_prediction.tif` — per-pixel class IDs.
- `disturbed_polygons.gpkg` — vector patches above area threshold.
- `disturbed_overlay.png` — before image with yellow outlines, red bounding box, and centroid GPS label (lat/lon).

## 8. Iterate
- If polygons are missing or too many, adjust:
  - `--prob-threshold` (lower → more detections).
  - `--min-patch-area-m2` (lower → keeps smaller patches).
- Update labels in `training_polygons.gpkg` and rerun to improve results.
