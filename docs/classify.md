# Classify mode

The original supervised mode. You supply a before raster, an after raster and polygons labeled by hand. A RandomForest learns what your "disturbed" class looks like and paints it across the scene.

Use it when you already know a handful of sites in a region and want to find their relatives. It is **not** a search tool for an area you know nothing about, because it needs labels for the very ground it is classifying. For that, use `dgd scan`.

## 1. Get two rasters

Either bring your own, co-registered, same grid, same CRS, or download Sentinel-2 composites:

```bash
dgd download --lat 41.0963 --lon 14.4009 --radius-m 1500 \
             --before-start 2024-06-01 --after-start 2025-06-01 --window-days 60 \
             --before-path before.tif --after-path after.tif
```

That writes 6-band composites (blue, green, red, nir, swir16, swir22). Red is band index 2 and NIR is index 3, the defaults.

The `before.tif` and `after.tif` in this repository are older 4-band composites (blue, green, red, nir) of a 3 x 3 km tile in Caserta province, Italy. Same red and NIR positions, so they work with the defaults too.

## 2. Draw labels

Polygons need an integer `class_id` column. Disturbed ground gets `--disturbed-class-id` (default 3). Everything else gets something else, typically 0.

To get a file with the right structure, generate placeholders and edit them in QGIS:

```bash
python create_training_polygons.py --raster before.tif --output training_polygons.gpkg
```

The placeholders are three boxes placed by fractions of the extent. They mean nothing. Train on them and act on the result, and you deserve what happens next.

In QGIS:

1. Add `before.tif` and `after.tif` as layers. Flick between them.
2. Open `training_polygons.gpkg`, toggle editing, delete the placeholders.
3. Draw polygons over known disturbed ground with `class_id = 3`, and over a variety of undisturbed ground (fields, trees, roads, roofs) with `class_id = 0`.
4. **Draw at least two polygons per class, better five or more.** Validation holds out whole polygons. With one polygon per class it falls back to a random pixel split, which grades the model on its memory and says so in `metrics.txt`.
5. Save. Keep the layer CRS the same as the raster, or let the tool reproject it.

## 3. Run

```bash
dgd classify --before-path before.tif --after-path after.tif \
             --training-path training_polygons.gpkg \
             --output-dir outputs/classify_demo
```

## 4. Outputs

| File | What |
| --- | --- |
| `metrics.txt` | Validation report and confusion matrix, plus which split was used, and centroids of detections with mean probability. |
| `disturbed_probability.tif` | Probability of the disturbed class, per pixel. |
| `disturbed_binary.tif` | 1 disturbed, 0 not, 255 no data. |
| `class_prediction.tif` | Predicted class id per pixel, -1 no data. |
| `disturbed_polygons.gpkg` | Patches above `--min-patch-area-m2`, with mean probability. |
| `disturbed_overlay.png` | Before image, yellow outlines, red box, centroid coordinates. |

## 5. Iterate

- Too many detections: raise `--prob-threshold` or `--min-patch-area-m2`, and add background polygons on whatever got falsely flagged.
- Too few: lower them, and add disturbed polygons.
- Validation numbers on held-out polygons are the honest ones. If they are bad, more varied labels help more than any setting.
