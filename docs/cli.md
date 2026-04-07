# CLI Reference

Run with `python main.py [options]`.

## Core paths
| Argument | Default | Description |
| --- | --- | --- |
| `--before-path` | `before.tif` | Path to pre‑event raster. |
| `--after-path` | `after.tif` | Path to post‑event raster. |
| `--training-path` | `training_polygons.gpkg` | Vector labels containing a `class_id` column. |
| `--output-dir` | `outputs` | Directory for all generated files. |

## Classes & thresholds
| Argument | Default | Description |
| --- | --- | --- |
| `--disturbed-class-id` | `3` | Label value representing disturbed ground. |
| `--prob-threshold` | `0.60` | Probability cutoff to create the binary disturbed mask. |
| `--min-patch-area-m2` | `400` | Filter out polygons smaller than this area. |

## Band selection
| Argument | Default | Description |
| --- | --- | --- |
| `--red-band-index` | `2` | Zero‑based index for red band. |
| `--nir-band-index` | `3` | Zero‑based index for NIR band. |

## Sentinel download (enable with `--download-sentinel`)
| Argument | Default | Description |
| --- | --- | --- |
| `--download-sentinel` | flag | Trigger Sentinel‑2 export via Earth Engine. |
| `--before-date` | required | Start date (YYYY‑MM‑DD) for before composite. |
| `--after-date` | required | Start date (YYYY‑MM‑DD) for after composite. |
| `--lat` | required | AOI center latitude. |
| `--long` | required | AOI center longitude. |
| `--buffer-m` | `1500` | Half‑width of AOI box in meters. |
| `--window-days` | `60` | Composite window length starting at each date. |
| `--cloud-pct` | `20` | Max `CLOUDY_PIXEL_PERCENTAGE`. |
| `--scale` | `10` | Export pixel size in meters. |
| *(no overlay-specific flags)* |  | Overlay uses full raster extent with default sizing. |
| `--no-progress` | flag | Disable tqdm progress bar output. |

## Outputs
- `class_prediction.tif` (int16, nodata = -1)
- `disturbed_probability.tif` (float32)
- `disturbed_binary.tif` (uint8)
- `disturbed_polygons.gpkg`
- `disturbed_overlay.png`
- `metrics.txt`

## Notes
- Training data CRS is reprojected to raster CRS when needed.
- Band indices must be within the raster band count.
- If no polygons are produced, try lowering `--prob-threshold` or `--min-patch-area-m2`.
- TQDM progress is enabled by default; add `--no-progress` if you prefer quiet logs.
- Run locally without installing by prepending `PYTHONPATH=src` (e.g., `PYTHONPATH=src python main.py`) or use the helper `python run_cli.py`.

*(TUI removed; use the CLI instructions above.)*
