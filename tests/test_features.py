import numpy as np

from dgd.features import build_feature_stack, normalized_difference, safe_divide, spectral_indices


def test_safe_divide_handles_zero_and_nan():
    a = np.array([1.0, 2.0, np.nan], dtype=np.float32)
    b = np.array([1.0, 0.0, 2.0], dtype=np.float32)
    out = safe_divide(a, b)
    assert np.isclose(out[0], 1.0)
    assert np.isnan(out[1])
    assert np.isnan(out[2])


def test_normalized_difference_matches_manual():
    a = np.array([3.0, 5.0], dtype=np.float32)
    b = np.array([1.0, 1.0], dtype=np.float32)
    nd = normalized_difference(a, b)
    expected = (a - b) / (a + b)
    assert np.allclose(nd, expected)


def test_build_feature_stack_shapes_and_content():
    before = np.ones((4, 2, 2), dtype=np.float32)
    after = before + 1.0
    stack = build_feature_stack(before, after, red_band_index=2, nir_band_index=3)

    # Original bands (4 before + 4 after) + delta(4) + ndvi(3) + brightness(3) = 18
    assert stack.shape == (18, 2, 2)
    # Brightness difference should be 1 everywhere
    assert np.allclose(stack[-1], 1.0)


def test_spectral_indices_tell_grass_from_soil():
    grass = np.array([0.03, 0.07, 0.04, 0.40, 0.20, 0.10], dtype=np.float32)[:, None]
    soil = np.array([0.08, 0.11, 0.15, 0.22, 0.30, 0.24], dtype=np.float32)[:, None]
    g, s = spectral_indices(grass), spectral_indices(soil)
    assert g["ndvi"][0] > 0.7 and s["ndvi"][0] < 0.3
    assert s["bsi"][0] > g["bsi"][0]
    assert s["nbr"][0] < g["nbr"][0]
    assert set(g) == {"ndvi", "bsi", "nbr", "bright"}
