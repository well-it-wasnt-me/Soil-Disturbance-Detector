import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import box  # type: ignore
from rasterio.transform import from_origin  # type: ignore

from dgd.model import evaluate_model, predict_full_image, train_classifier
from dgd.sampling import extract_training_samples


def _synthetic_stack():
    """Create a simple 3-feature stack with a separable region."""
    rows = cols = 4
    stack = np.zeros((3, rows, cols), dtype=np.float32)
    # Mark top-left quadrant with higher values
    stack[:, 0:2, 0:2] = 5.0
    return stack


def _training_labels(transform):
    disturbed = box(0, 0, 2, 2)  # matches the high-valued quadrant
    background = box(2, 2, 4, 4)
    gdf = gpd.GeoDataFrame(
        {"class_id": [3, 0]},
        geometry=[disturbed, background],
        crs="EPSG:3857",
    )
    return gdf


def test_extract_training_samples_produces_two_classes():
    stack = _synthetic_stack()
    transform = from_origin(0, 4, 1, 1)
    gdf = _training_labels(transform)

    x, y = extract_training_samples(stack, transform, gdf)
    assert set(np.unique(y)) == {0, 3}
    assert x.shape[0] == y.shape[0]


def test_train_and_predict_full_image():
    stack = _synthetic_stack()
    transform = from_origin(0, 4, 1, 1)
    gdf = _training_labels(transform)
    x, y = extract_training_samples(stack, transform, gdf)

    clf = train_classifier(x, y)
    report, cm = evaluate_model(clf, x, y)
    assert "precision" in report
    assert cm.shape == (2, 2)

    pred, proba = predict_full_image(clf, stack, disturbed_class_id=3)
    assert pred.shape == (4, 4)
    assert proba.shape == (4, 4)
    assert np.nanmax(proba) <= 1.0
    assert np.nanmin(proba) >= 0.0
