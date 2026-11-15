"""Supervised before/after classification (``dgd classify``).

You bring two co-registered rasters and polygons labeled with a class_id.
A RandomForest learns what your "disturbed" class looks like and paints it
across the whole scene.

Use it when you already know a few sites in a region and want more like
them. It is not a search tool for a region you know nothing about. For that
use ``dgd scan``, which needs no labels at all.
"""

from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
from rasterio.features import geometry_mask
from sklearn.model_selection import train_test_split

from dgd.features import build_feature_stack
from dgd.model import evaluate_model, predict_full_image, train_classifier
from dgd.progress import NullProgress, TqdmProgress
from dgd.raster_io import apply_nodata, read_raster, save_raster
from dgd.sampling import extract_training_samples
from dgd.vectorization import raster_to_polygons
from dgd.visualization import save_overlay

log = logging.getLogger(__name__)

# 255 means "no data" in the binary mask. Zero means "not disturbed", and
# those are two very different statements to anyone opening it in QGIS.
BINARY_NODATA = 255


class DisturbanceDetector:
    """Pipeline coordinator for classify mode.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed ``dgd classify`` arguments.
    progress : optional
        Something with ``step(message)`` and ``close()``.
    """

    STEPS = 10

    def __init__(self, args, progress=None):
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.metrics_path = self.output_dir / "metrics.txt"
        self.progress = progress or NullProgress()

    def run(self) -> None:
        """Load, train, validate, predict, vectorize, export. In that order."""

        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.progress.step("Reading rasters")
            before_arr, before_profile, transform, crs, before_nodata = read_raster(self.args.before_path)
            after_arr, _after_profile, _, after_crs, after_nodata = read_raster(self.args.after_path)

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
            x, y, groups = extract_training_samples(feature_stack, transform, training_gdf, return_groups=True)
            x_train, x_valid, y_train, y_valid, split_note = self._split(x, y, groups)

            self.progress.step("Training and validating model")
            clf = train_classifier(x_train, y_train)
            report, cm = evaluate_model(clf, x_valid, y_valid)
            self._write_metrics(report, cm, split_note)

            self.progress.step("Predicting full scene")
            class_pred, disturbed_proba = predict_full_image(clf, feature_stack, self.args.disturbed_class_id)
            disturbed_binary = (disturbed_proba >= self.args.prob_threshold).astype(np.uint8)
            disturbed_binary[np.isnan(disturbed_proba)] = BINARY_NODATA

            self.progress.step("Saving rasters")
            self._save_raster_outputs(before_profile, class_pred, disturbed_proba, disturbed_binary)

            self.progress.step("Vectorizing polygons")
            disturbed_gdf = raster_to_polygons(
                (disturbed_binary == 1).astype(np.uint8),
                transform,
                crs,
                min_patch_area_m2=self.args.min_patch_area_m2,
            )

            self.progress.step("Exporting stats and overlay")
            if len(disturbed_gdf):
                self._export_polygons_and_overlay(disturbed_gdf, transform, disturbed_binary, disturbed_proba)
            else:
                print("No disturbed polygons above the area threshold. Lower --prob-threshold or --min-patch-area-m2.")

            print("Outputs written to:", self.output_dir)
        finally:
            self.progress.close()

    def _split(self, x: np.ndarray, y: np.ndarray, groups: np.ndarray):
        """Hold out whole polygons when possible.

        Splitting pixels at random puts a pixel's next-door neighbor in the
        training set and grades the model on how well it remembers. That
        number looks great and means nothing. Holding out entire polygons is
        the honest version, so it is the default whenever every class has at
        least two polygons to spare one.
        """
        classes = np.unique(y)
        by_class = {c: np.unique(groups[y == c]) for c in classes}
        n_groups = len(np.unique(groups))
        # Validation needs at least one polygon of every class, or the report
        # is grading the model on a subject it never sat. Training needs one
        # too. So one polygon per class is set aside for each side first, and
        # the rest of the validation share is drawn from what is left. With
        # two polygons per class that always works, no lucky seed needed.
        short = [str(c) for c in classes if len(by_class[c]) < 2]
        if not short:
            # A quarter of the polygons, and at least one per class. Two per
            # class means at least 2k polygons, so k still stay for training.
            test_n = max(int(round(0.25 * n_groups)), len(classes))
            rng = np.random.default_rng(42)
            val, keep = set(), set()
            for c in classes:
                g = rng.permutation(by_class[c])
                val.add(g[0])
                keep.add(g[1])
            rest = [g for g in np.unique(groups) if g not in val and g not in keep]
            val.update(rng.permutation(rest)[: max(0, test_n - len(val))].tolist())
            va = np.isin(groups, list(val))
            return x[~va], x[va], y[~va], y[va], "validation on held-out polygons"
        log.warning(
            "Class %s has fewer than two polygons, so validation falls back to a random pixel split. "
            "Expect the numbers in metrics.txt to be optimistic. Draw more polygons.", ", ".join(short)
        )
        x_tr, x_va, y_tr, y_va = train_test_split(x, y, test_size=0.2, random_state=42, stratify=y)
        return x_tr, x_va, y_tr, y_va, "validation on random pixels (OPTIMISTIC, neighbors leak)"

    def _validate_rasters(self, before_arr: np.ndarray, after_arr: np.ndarray, crs, after_crs) -> None:
        if before_arr.shape != after_arr.shape:
            raise ValueError("Before and after rasters must have the same shape.")
        if crs != after_crs:
            raise ValueError("Before and after rasters must share the same CRS.")

    def _validate_band_indices(self, arr: np.ndarray) -> None:
        n = arr.shape[0]
        for name in ("red_band_index", "nir_band_index"):
            idx = getattr(self.args, name)
            if not 0 <= idx < n:
                raise ValueError(f"--{name.replace('_', '-')} {idx} is outside the raster's {n} bands.")

    def _load_training_data(self, crs) -> gpd.GeoDataFrame:
        training_gdf = gpd.read_file(self.args.training_path)
        if "class_id" not in training_gdf.columns:
            raise ValueError("Training data must include a 'class_id' column.")
        if training_gdf.crs != crs:
            training_gdf = training_gdf.to_crs(crs)
        return training_gdf

    def _write_metrics(self, report: str, cm: np.ndarray, split_note: str = "") -> None:
        print(f"Validation ({split_note}):")
        print(report)
        print("Confusion matrix:")
        print(cm)
        self.metrics_path.write_text(
            f"Validation: {split_note}\n\n" + report + "\nConfusion matrix:\n" + np.array2string(cm) + "\n",
            encoding="utf-8",
        )

    def _save_raster_outputs(self, profile: dict, class_pred, disturbed_proba, disturbed_binary) -> None:
        save_raster(self.output_dir / "class_prediction.tif", class_pred, profile, "int16", nodata=-1)
        save_raster(self.output_dir / "disturbed_probability.tif", disturbed_proba, profile, "float32", nodata=np.nan)
        save_raster(self.output_dir / "disturbed_binary.tif", disturbed_binary, profile, "uint8", nodata=BINARY_NODATA)

    def _export_polygons_and_overlay(self, disturbed_gdf, transform, disturbed_binary, disturbed_proba) -> None:
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

    def _append_centroids_to_metrics(self, disturbed_gdf, mean_probs: list[float]) -> None:
        try:
            # Centroids in a metric CRS, then to lat/lon. Taking centroids in
            # degrees is the kind of shortcut that comes back to bite you.
            centroids_ll = disturbed_gdf.to_crs(disturbed_gdf.estimate_utm_crs()).centroid.to_crs("EPSG:4326")
            lines = ["", "Disturbed polygon centroids with mean probability (lat, lon, mean_prob):"]
            for i, (pt, prob) in enumerate(zip(centroids_ll, mean_probs), start=1):
                prob_txt = f"{prob:.3f}" if np.isfinite(prob) else "nan"
                lines.append(f"- {i}: {pt.y:.6f}, {pt.x:.6f}, {prob_txt}")
            with self.metrics_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as exc:
            print(f"Failed to append centroid coords to metrics.txt: {exc}")

    def _build_overlay(self, disturbed_gdf) -> None:
        try:
            save_overlay(self.args.before_path, disturbed_gdf, self.output_dir / "disturbed_overlay.png", self.args.red_band_index)
        except Exception as exc:
            print(f"Failed to create overlay PNG: {exc}")


__all__ = ["BINARY_NODATA", "DisturbanceDetector", "NullProgress", "TqdmProgress"]
