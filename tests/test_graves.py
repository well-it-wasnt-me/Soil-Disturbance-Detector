import json
import math
import xml.etree.ElementTree as ET

import numpy as np
import pytest

rasterio = pytest.importorskip("rasterio")
from rasterio.enums import ColorInterp  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402

from dgd.graves import access_weight, features, find_graves, gather_evidence, local_rx, shape_fit  # noqa: E402
from dgd.highres import (  # noqa: E402
    HRImage,
    coregister,
    grid_for_raster,
    grid_from_bounds,
    match_histograms,
    parse_bands,
    phase_correlate,
    read_to_grid,
)

RES = 0.1
GRASS = np.array([0.20, 0.30, 0.15, 0.55], dtype=np.float32)  # red, green, blue, nir
SOIL = np.array([0.35, 0.30, 0.25, 0.32], dtype=np.float32)


def _lawn(size=400, seed=0):
    """Grass with clumpy texture. Real lawns are lumpy at 10 to 50 cm, not salt and pepper."""
    from scipy.ndimage import gaussian_filter

    rng = np.random.default_rng(seed)
    clumps = gaussian_filter(rng.standard_normal((size, size)), 3)
    clumps /= clumps.std()
    tex = 1 + 0.10 * clumps + 0.04 * rng.standard_normal((size, size))
    # Each band gets its own noise too, like a real sensor. Without it every
    # ratio index is perfectly flat and any patch becomes a 50 million sigma event.
    per_band = 1 + 0.04 * rng.standard_normal((4, size, size))
    return (GRASS[:, None, None] * tex[None] * per_band).astype(np.float32)


def _paint_rect(data, r0, c0, h, w, spectrum=SOIL):
    data[:, r0:r0 + h, c0:c0 + w] = spectrum[:, None, None]


def _image(data, roles=("red", "green", "blue", "nir")):
    grid = grid_from_bounds("EPSG:32633", 500000, 4500000, 500000 + data.shape[2] * RES, 4500000 + data.shape[1] * RES, RES)
    return HRImage(data=data.astype(np.float32), grid=grid, roles={r: i for i, r in enumerate(roles)}, source="test")


def test_parse_bands_presets_lists_and_errors():
    assert parse_bands(None, 4) == {"red": 0, "green": 1, "blue": 2, "nir": 3}
    assert parse_bands("sequoia", 4) == {"green": 0, "red": 1, "rededge": 2, "nir": 3}
    assert parse_bands("blue,green,red,skip,nir", 5)["nir"] == 4
    with pytest.raises(ValueError):
        parse_bands("rgbn", 3)
    with pytest.raises(ValueError):
        parse_bands("purple,green,red", 3)
    with pytest.raises(ValueError):
        parse_bands(None, 1)


def test_features_without_blue_and_with_red_edge():
    data = np.stack([GRASS[1], GRASS[0], 0.45, GRASS[3]])[:, None, None] * np.ones((1, 4, 4), np.float32)
    img = _image(data, roles=("green", "red", "rededge", "nir"))
    f = features(img)
    assert {"bright", "soil", "ndvi", "ndre"} <= set(f) and "exg" not in f
    assert img.display_bands() == [3, 1, 0]  # color infrared when there is no blue


def test_phase_correlation_and_coregistration_recover_a_shift():
    rng = np.random.default_rng(1)
    base = np.kron(rng.random((40, 40)), np.ones((5, 5))).astype(np.float32)
    from scipy.ndimage import shift

    moved = shift(base, (3.0, -2.0), order=1, mode="nearest")
    dy, dx = phase_correlate(base, moved)
    assert dy == pytest.approx(-3.0, abs=0.3) and dx == pytest.approx(2.0, abs=0.3)
    a = _image(np.stack([base, base, base]), ("red", "green", "blue"))
    b = _image(np.stack([moved, moved, moved]), ("red", "green", "blue"))
    b = coregister(b, a)
    assert "co-registered" in b.notes[0]
    inner = (slice(10, -10), slice(10, -10))
    assert np.nanmean(np.abs(b.data[0][inner] - base[inner])) < 0.05


def test_match_histograms_aligns_colors():
    a = _image(_lawn(seed=2))
    b = _image(_lawn(seed=3) * 1.4)
    b = match_histograms(b, a)
    assert np.nanmedian(b.data[1]) == pytest.approx(np.nanmedian(a.data[1]), rel=0.02)


def test_local_rx_lights_up_a_patch():
    data = _lawn(200)
    _paint_rect(data, 90, 90, 20, 8)
    f = features(_image(data))
    rx = local_rx(np.stack(list(f.values())), RES)
    assert np.nanargmax(rx) // 200 in range(85, 115)
    assert np.nanmax(rx[90:110, 90:98]) > 3 * np.nanmedian(rx)


def test_shape_and_access_helpers():
    assert shape_fit(2.0, 0.8, 0.1) == 1.0
    assert shape_fit(1.3, 1.2, 0.1) < 0.5  # roundish: a dung patch, not a grave
    assert shape_fit(9.0, 4.0, 0.1) == 0.0
    assert access_weight(0) == pytest.approx(1.0) and access_weight(1000) == pytest.approx(0.3, abs=0.01)
    assert access_weight(None) == 1.0


def test_a_grave_beats_a_dung_patch():
    data = _lawn(400, seed=4)
    _paint_rect(data, 100, 100, 20, 8)  # 2.0 x 0.8 m grave
    _paint_rect(data, 280, 280, 13, 13)  # 1.3 x 1.3 m round-ish patch, same soil
    img = _image(data)
    ev = gather_evidence(img, mask=np.ones(img.grid.shape, bool))
    _, cands = find_graves(ev, img.grid, 3.0)
    assert cands, "found nothing at all"
    top = cands[0]
    assert top.rows.start <= 110 <= top.rows.stop and top.cols.start <= 104 <= top.cols.stop
    # Sizes describe the detection footprint, which the detector windows widen by
    # roughly a quarter metre on each side. Documented, so it is tested as such.
    assert 2.0 <= top.length_m <= 2.8 and 0.8 <= top.width_m <= 1.7
    assert top.id == "G001"
    patch = [c for c in cands if c.rows.start <= 286 <= c.rows.stop and c.cols.start <= 286 <= c.cols.stop]
    assert not patch or patch[0].rank > top.rank


def test_change_detection_ignores_what_was_already_there():
    before = _lawn(300, seed=5)
    _paint_rect(before, 50, 50, 20, 8)  # old bare patch, present in both
    after = before.copy() * 1.02
    _paint_rect(after, 200, 200, 20, 8)  # new
    a, b = _image(after), _image(before)
    ev = gather_evidence(a, b, mask=np.ones(a.grid.shape, bool))
    _, cands = find_graves(ev, a.grid, 3.0)
    assert cands and cands[0].rows.start <= 210 <= cands[0].rows.stop
    assert "change" in ev.layers


def _write(path, data, alpha_tag=False, dtype="uint8"):
    arr = (np.clip(data, 0, 1) * 255).astype(dtype) if dtype == "uint8" else data.astype(dtype)
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[1], width=arr.shape[2], count=arr.shape[0], dtype=dtype,
                       crs="EPSG:32633", transform=from_origin(500000, 4500000 + arr.shape[1] * RES, RES, RES)) as dst:
        dst.write(arr)
        if alpha_tag:
            dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha]
    return path


def test_nir_tagged_as_alpha_is_rescued_and_real_alpha_is_respected(tmp_path):
    data = _lawn(64)
    p = _write(tmp_path / "rgbn.tif", data, alpha_tag=True)
    img = read_to_grid(str(p), grid_for_raster(str(p)))
    assert img.has_nir and img.data.shape[0] == 4
    rgba = np.concatenate([data[:3], np.ones((1, 64, 64), np.float32)])
    rgba[3, :10] = 0  # transparent strip
    p2 = _write(tmp_path / "rgba.tif", rgba, alpha_tag=True)
    img2 = read_to_grid(str(p2), grid_for_raster(str(p2)))
    assert not img2.has_nir and np.isnan(img2.data[0, 0]).all() and np.isfinite(img2.data[0, 20]).all()


def test_search_end_to_end_writes_gpx_kml_and_manifest(tmp_path):
    from dgd.grave_report import write_all
    from dgd.grave_search import GraveSearchConfig, search

    data = _lawn(300, seed=6)
    _paint_rect(data, 140, 140, 20, 8)
    after = _write(tmp_path / "after.tif", data)
    cfg = GraveSearchConfig(after_path=str(after), output_dir=tmp_path / "out", use_osm=False, name="Test search", max_chips=3)
    result = search(cfg)
    assert result.candidates and result.candidates[0].id == "G001"
    out = write_all(result)
    for name in ("report.html", "candidates.gpx", "candidates.kml", "candidates.csv", "candidates.geojson", "evidence.tif", "manifest.json"):
        assert (tmp_path / "out" / name).exists(), name
        if not name.endswith(".tif"):
            (tmp_path / "out" / name).read_bytes().decode("ascii")
    gpx = ET.parse(tmp_path / "out" / "candidates.gpx").getroot()
    wpts = [e for e in gpx if e.tag.endswith("wpt")]
    assert len(wpts) == len(result.candidates)
    ET.parse(tmp_path / "out" / "candidates.kml")
    manifest = json.loads((tmp_path / "out" / "manifest.json").read_text())
    assert manifest["inputs"]["after"]["sha256"] and "Do not dig" in manifest["scene_rules"]
    assert "Do not dig" in (tmp_path / "out" / "report.html").read_text()
    assert (tmp_path / "out" / "chips" / "G001.png").exists() and "report.html" in out
    c = result.candidates[0]
    assert math.isfinite(c.lat) and math.isfinite(c.lon)


def test_graves_cli_needs_an_image():
    from dgd import cli

    with pytest.raises(SystemExit, match="--after IMAGE"):
        cli.main(["graves", "--no-progress"])
    with pytest.raises(SystemExit, match="--fetch needs"):
        cli.main(["graves", "--fetch", "pdok", "--no-progress"])


def test_mask_out_removes_candidates(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box

    from dgd.grave_search import GraveSearchConfig, search

    data = _lawn(300, seed=8)
    _paint_rect(data, 140, 140, 20, 8)
    after = _write(tmp_path / "after.tif", data)
    # Cover the planted grave with an exclusion polygon: it must vanish.
    x0, y_top = 500000, 4500000 + 300 * RES
    poly = box(x0 + 130 * RES, y_top - 170 * RES, x0 + 160 * RES, y_top - 130 * RES)
    gpd.GeoDataFrame(geometry=[poly], crs="EPSG:32633").to_file(tmp_path / "skip.geojson", driver="GeoJSON")
    base = dict(after_path=str(after), output_dir=tmp_path / "o", use_osm=False)
    with_grave = search(GraveSearchConfig(**base))
    without = search(GraveSearchConfig(**base, mask_out_path=str(tmp_path / "skip.geojson")))
    hit = lambda cands: any(c.rows.start <= 150 <= c.rows.stop and c.cols.start <= 144 <= c.cols.stop for c in cands)  # noqa: E731
    assert hit(with_grave.candidates) and not hit(without.candidates)
    assert any("masked out" in n for n in without.notes)


@pytest.mark.parametrize("tagged", [True, False])
def test_band_order_can_name_the_alpha_band(tmp_path, tagged):
    rgba = np.concatenate([_lawn(64)[:3], np.ones((1, 64, 64), np.float32)])
    rgba[3, :10] = 0  # transparent strip
    p = _write(tmp_path / "rgba.tif", rgba, alpha_tag=tagged)
    img = read_to_grid(str(p), grid_for_raster(str(p)), bands="red,green,blue,alpha")
    assert not img.has_nir and np.isnan(img.data[0, 0]).all() and np.isfinite(img.data[0, 20]).all()
