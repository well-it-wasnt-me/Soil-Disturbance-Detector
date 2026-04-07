# Disturbed Ground Detector

Detects where ground was disturbed between two satellite images (before/after). The pipeline can optionally download Sentinel‑2 composites, train a RandomForest on your labeled polygons, and export rasters, polygons, and a visual overlay with a red bounding box plus GPS coordinates.

## Features
- Optional Sentinel‑2 download via Earth Engine.
- Feature stack with bands, deltas, NDVI, brightness.
- RandomForest classifier with balanced classes and validation report.
- Polygonization with area filtering.
- Overlay PNG highlighting detected disturbance with centroid lat/lon.

## Outputs
- `class_prediction.tif` — per‑pixel class IDs (nodata = -1)
- `disturbed_probability.tif` — probability of disturbed class
- `disturbed_binary.tif` — thresholded mask
- `disturbed_polygons.gpkg` — filtered polygons
- `disturbed_overlay.png` — red box, yellow outlines, centroid GPS label
- `metrics.txt` — classification report and confusion matrix

## Repository layout
- `main.py` — CLI entrypoint calling the packaged pipeline.
- `src/dgd/` — modular pipeline code (CLI, features, IO, model, vectorization, overlay, Sentinel download).
- `create_training_polygons.py` — helper to create a dummy `training_polygons.gpkg`.
- `requirements.txt` — dependencies (Earth Engine extras included) plus `pytest` for tests.
- `mkdocs.yml`, `docs/` — this documentation.
- `tests/` — unit tests (~90% coverage target).
