"""End-to-end disturbed ground detection pipeline.

This module houses the orchestration class that ties together raster IO,
feature engineering, sampling, model training, vectorization, and reporting
for the disturbed-ground detection workflow.
"""

from __future__ import annotations

from pathlib import Path
from typing import Tuple

import geopandas as gpd
import numpy as np
from rasterio.features import geometry_mask
from sklearn.model_selection import train_test_split

from dgd.features import build_feature_stack
from dgd.model import evaluate_model, predict_full_image, train_classifier
from dgd.raster_io import apply_nodata, read_raster, save_raster
from dgd.sampling import extract_training_samples
from dgd.sentinel import download_sentinel_pair
from dgd.vectorization import raster_to_polygons
from dgd.visualization import save_overlay


class NullProgress:
    """Progress stub used when progress bars are disabled."""

    def step(self, message: str) -> None:
        pass

    def close(self) -> None:
        pass


class TqdmProgress:
    """Lightweight tqdm wrapper for pipeline milestones."""

    def __init__(self, total_steps: int, label: str = "Pipeline"):
        from tqdm.auto import tqdm

        self.bar = tqdm(total=total_steps, desc=label, leave=False)

    def step(self, message: str) -> None:
        self.bar.write(message)
        self.bar.update(1)

    def close(self) -> None:
        self.bar.close()


class DisturbanceDetector:
    """Pipeline coordinator for disturbed-ground detection.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments produced by ``dgd.cli.parse_args``.

    Attributes
    ----------
    args : argparse.Namespace
        Stored CLI arguments for reuse across helper methods.
    output_dir : Path
        Destination directory for all generated artifacts.
    metrics_path : Path
        Location of the human-readable metrics text file.
    """

    def __init__(self, args, progress=None):
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.metrics_path = self.output_dir / "metrics.txt"
        self.progress = progress or NullProgress()

    def run(self) -> None:
        """Execute the full detection pipeline from data load to exports."""

        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.progress.step("Downloading Sentinel-2 composites" if self.args.download_sentinel else "Skipping download step")
            if self.args.download_sentinel:
                download_sentinel_pair(self.args)

            self.progress.step("Reading rasters")
            before_arr, before_profile, transform, crs, before_nodata = read_raster(self.args.before_path)
            after_arr, after_profile, _, after_crs, after_nodata = read_raster(self.args.after_path)

            self.progress.step("Validating rasters")
            self._validate_rasters(before_arr, after_arr, crs, after_crs)

            self.progress.step("Applying nodata masks")
            before_arr = apply_nodata(before_arr, before_nodata)
            after_arr = apply_nodata(after_arr, after_nodata)
            self._validate_band_indices(before_arr)

            self.progress.step("Building feature stack")
            feature_stack = build_feature_stack(
                before_arr,
                after_arr,
                red_band_index=self.args.red_band_index,
                nir_band_index=self.args.nir_band_index,
            )

            self.progress.step("Loading training data")
            training_gdf = self._load_training_data(crs)
            x, y = extract_training_samples(feature_stack, transform, training_gdf)
            x_train, x_valid, y_train, y_valid = train_test_split(
                x, y, test_size=0.2, random_state=42, stratify=y
            )

            self.progress.step("Training + validating model")
            clf = train_classifier(x_train, y_train)
            report, cm = evaluate_model(clf, x_valid, y_valid)
            self._write_metrics(report, cm)

            self.progress.step("Predicting full scene")
            class_pred, disturbed_proba = predict_full_image(clf, feature_stack, self.args.disturbed_class_id)
            disturbed_binary = (disturbed_proba >= self.args.prob_threshold).astype(np.uint8)
            disturbed_binary[np.isnan(disturbed_proba)] = 0

            self.progress.step("Saving rasters")
            self._save_raster_outputs(before_profile, class_pred, disturbed_proba, disturbed_binary)

            self.progress.step("Vectorizing polygons")
            disturbed_gdf = raster_to_polygons(
                disturbed_binary,
                transform,
                crs,
                min_patch_area_m2=self.args.min_patch_area_m2,
            )

            self.progress.step("Exporting stats + overlay")
            if len(disturbed_gdf):
                self._export_polygons_and_overlay(disturbed_gdf, transform, disturbed_binary, disturbed_proba)
            else:
                print("No disturbed polygons found above the area threshold.")

            print("Outputs written to:", self.output_dir)
        finally:
            self.progress.close()

    def _validate_rasters(self, before_arr: np.ndarray, after_arr: np.ndarray, crs, after_crs) -> None:
        """Confirm spatial compatibility of before/after rasters."""

        if before_arr.shape != after_arr.shape:
            raise ValueError("Before and after rasters must have the same shape.")
        if crs != after_crs:
            raise ValueError("Before and after rasters must share the same CRS.")

    def _validate_band_indices(self, arr: np.ndarray) -> None:
        """Ensure configured band indices exist in the raster stack."""

        if self.args.red_band_index >= arr.shape[0] or self.args.nir_band_index >= arr.shape[0]:
            raise ValueError("Band indices are outside the raster band count.")

    def _load_training_data(self, crs) -> gpd.GeoDataFrame:
        """Read and CRS-align training polygons."""

        training_gdf = gpd.read_file(self.args.training_path)
        if "class_id" not in training_gdf.columns:
            raise ValueError("Training data must include a 'class_id' column.")
        if training_gdf.crs != crs:
            training_gdf = training_gdf.to_crs(crs)
        return training_gdf

    def _write_metrics(self, report: str, cm: np.ndarray) -> None:
        """Persist validation metrics and print them to stdout."""

        print("Validation classification report:")
        print(report)
        print("Confusion matrix:")
        print(cm)

        self.metrics_path.write_text(
            report + "\nConfusion matrix:\n" + np.array2string(cm),
            encoding="utf-8",
        )

    def _save_raster_outputs(
        self,
        profile: dict,
        class_pred: np.ndarray,
        disturbed_proba: np.ndarray,
        disturbed_binary: np.ndarray,
    ) -> None:
        """Write classification rasters to disk."""

        save_raster(self.output_dir / "class_prediction.tif", class_pred, profile, "int16", nodata=-1)
        save_raster(
            self.output_dir / "disturbed_probability.tif",
            disturbed_proba,
            profile,
            "float32",
            nodata=np.nan,
        )
        save_raster(self.output_dir / "disturbed_binary.tif", disturbed_binary, profile, "uint8", nodata=0)

    def _export_polygons_and_overlay(
        self,
        disturbed_gdf: gpd.GeoDataFrame,
        transform,
        disturbed_binary: np.ndarray,
        disturbed_proba: np.ndarray,
    ) -> None:
        """Vectorize detections, compute per-polygon stats, and build overlay."""

        rows, cols = disturbed_binary.shape
        mean_probs = []
        for geom in disturbed_gdf.geometry:
            mask = geometry_mask([geom], transform=transform, invert=True, out_shape=(rows, cols))
            vals = disturbed_proba[mask]
            vals = vals[np.isfinite(vals)]
            mean_probs.append(float(vals.mean()) if len(vals) else float("nan"))

        disturbed_gdf["prob_threshold"] = self.args.prob_threshold
        disturbed_gdf["mean_prob"] = mean_probs
        disturbed_gdf.to_file(self.output_dir / "disturbed_polygons.gpkg", driver="GPKG")
        print(f"Saved {len(disturbed_gdf)} polygons to {self.output_dir / 'disturbed_polygons.gpkg'}")

        self._append_centroids_to_metrics(disturbed_gdf, mean_probs)
        self._build_overlay(disturbed_gdf)

    def _append_centroids_to_metrics(self, disturbed_gdf: gpd.GeoDataFrame, mean_probs: list[float]) -> None:
        """Append centroid coordinates and mean probabilities to metrics file."""

        try:
            centroids_ll = disturbed_gdf.to_crs("EPSG:4326").centroid
            lines = ["", "Disturbed polygon centroids with mean probability (lat, lon, mean_prob):"]
            for i, (pt, prob) in enumerate(zip(centroids_ll, mean_probs), start=1):
                prob_txt = f"{prob:.3f}" if np.isfinite(prob) else "nan"
                lines.append(f"- {i}: {pt.y:.6f}, {pt.x:.6f}, {prob_txt}")
            with self.metrics_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            print(f"Failed to append centroid coords to metrics.txt: {exc}")

    def _build_overlay(self, disturbed_gdf: gpd.GeoDataFrame) -> None:
        """Render and save a PNG overlay of detected disturbance."""

        try:
            overlay_path = self.output_dir / "disturbed_overlay.png"
            save_overlay(
                self.args.before_path,
                disturbed_gdf,
                overlay_path,
                self.args.red_band_index,
            )
        except Exception as exc:
            print(f"Failed to create overlay PNG: {exc}")


__all__ = ["DisturbanceDetector", "NullProgress", "TqdmProgress"]
