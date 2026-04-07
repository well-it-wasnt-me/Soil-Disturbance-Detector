"""Model training and inference helpers."""

from __future__ import annotations

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, confusion_matrix


def train_classifier(x_train: np.ndarray, y_train: np.ndarray) -> RandomForestClassifier:
    """Train a balanced RandomForest classifier for pixel labels."""

    clf = RandomForestClassifier(
        n_estimators=300,
        max_features="sqrt",
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    clf.fit(x_train, y_train)
    return clf


def evaluate_model(clf: RandomForestClassifier, x_valid: np.ndarray, y_valid: np.ndarray):
    """Compute validation metrics (classification report + confusion matrix)."""

    y_pred = clf.predict(x_valid)
    report = classification_report(y_valid, y_pred, digits=3)
    cm = confusion_matrix(y_valid, y_pred)
    return report, cm


def predict_full_image(clf: RandomForestClassifier, feature_stack: np.ndarray, disturbed_class_id: int):
    """Predict per-pixel classes and disturbed probability for full scene."""

    n_features, rows, cols = feature_stack.shape
    flat = feature_stack.reshape(n_features, rows * cols).T
    valid = np.isfinite(flat).all(axis=1)

    pred = np.full(rows * cols, -1, dtype=np.int16)
    disturbed_proba = np.full(rows * cols, np.nan, dtype=np.float32)

    pred[valid] = clf.predict(flat[valid])

    class_list = list(clf.classes_)
    if disturbed_class_id not in class_list:
        raise ValueError(f"Disturbed class {disturbed_class_id} not present in trained model classes: {class_list}")

    disturbed_idx = class_list.index(disturbed_class_id)
    disturbed_proba[valid] = clf.predict_proba(flat[valid])[:, disturbed_idx]

    return pred.reshape(rows, cols), disturbed_proba.reshape(rows, cols)


__all__ = ["evaluate_model", "predict_full_image", "train_classifier"]
