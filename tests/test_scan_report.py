import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from dgd.report import write_all
from dgd.scan import ScanConfig, run_scan


def _grass_landcover(grid, cache_dir=None):
    lc = np.full(grid.shape, 30, dtype=np.uint8)
    lc[-3:, :] = 80  # a strip of water along the bottom edge, excluded by default
    return lc


def _inside(cand, region):
    rows, cols = region
    return rows.start <= (cand.rows.start + cand.rows.stop) / 2 <= rows.stop and cols.start <= (cand.cols.start + cand.cols.stop) / 2 <= cols.stop


@pytest.fixture
def scan_result(synthetic_world, tmp_path):
    grid, ts, te, source, regions = synthetic_world
    cfg = ScanConfig(grid=grid, target_start=ts, target_end=te, output_dir=tmp_path / "out", name="Synthetic test", max_chips=5)
    return run_scan(cfg, source, landcover_loader=_grass_landcover), regions


def test_scan_finds_the_dump_and_the_dig_but_not_the_farmer(scan_result):
    result, regions = scan_result
    persistent = [c for c in result.candidates if c.kind == "persistent"]
    transient = [c for c in result.candidates if c.kind == "transient"]
    assert persistent and _inside(persistent[0], regions["dump"]), "the dump should be the top persistent lead"
    assert persistent[0].id == "P001"
    assert any(_inside(c, regions["dig"]) for c in transient), "the dig-and-cover should show up as transient"
    assert not any(_inside(c, regions["plowed"]) for c in result.candidates), "annual plowing is not news"
    p = persistent[0]
    assert p.first_seen is not None and p.first_seen >= result.config.target_start
    assert p.last_clear is not None and p.last_clear < p.first_seen
    assert len(p.timeseries) == len(result.baseline) + len(result.target)
    # The clip's "after" image is built from passes after the dump arrived.
    assert p.after_rgb is not None and p.after_rgb.shape[0] == 3
    assert str(p.first_seen) in p.after_label
    assert result.rasters["score"].shape == result.config.grid.shape


def test_reports_are_written_ascii_and_hashed(scan_result):
    result, _ = scan_result
    outputs = write_all(result)
    out = result.config.output_dir
    for name in ("report.html", "candidates.csv", "candidates.geojson", "candidates.kml", "manifest.json",
                 "overview.png", "score.tif", "persistence.tif", "event.tif", "candidates_mask.tif"):
        assert (out / name).exists(), name
    assert (out / "chips" / "P001.png").exists()
    for name in ("report.html", "candidates.csv", "candidates.geojson", "candidates.kml", "manifest.json"):
        (out / name).read_bytes().decode("ascii")  # raises if anything non-ASCII slipped in
    ET.parse(out / "candidates.kml")
    gj = json.loads((out / "candidates.geojson").read_text())
    assert len(gj["features"]) == len(result.candidates)
    assert {"id", "kind", "lat", "lon", "maps_url"} <= set(gj["features"][0]["properties"])
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["candidates"]["total"] == len(result.candidates)
    assert len(manifest["imagery"]["target_passes"]) == len(result.target)
    assert "candidates.csv" in manifest["outputs_sha256"] and len(manifest["outputs_sha256"]["candidates.csv"]) == 64
    assert "leads, not evidence" in manifest["disclaimer"]
    assert set(outputs) >= {"report.html", "manifest.json"}


def test_scan_refuses_when_there_is_no_imagery(synthetic_world, tmp_path):
    from datetime import date

    grid, _, _, source, _ = synthetic_world
    cfg = ScanConfig(grid=grid, target_start=date(2030, 1, 1), target_end=date(2030, 6, 1), output_dir=tmp_path)
    with pytest.raises(RuntimeError, match="target window"):
        run_scan(cfg, source, landcover_loader=_grass_landcover)


def test_scan_with_nothing_to_find_writes_an_empty_report(small_grid, tmp_path):
    from datetime import date

    from conftest import GRASS, FakeSource, pass_dates

    frames = {}
    for year in (2021, 2022, 2023, 2024):
        for d in pass_dates(date(year, 4, 1), date(year, 9, 30), every=10):
            frames[d] = np.broadcast_to(GRASS[:, None, None], (6, *small_grid.shape)).astype(np.float32).copy()
    cfg = ScanConfig(grid=small_grid, target_start=date(2024, 4, 1), target_end=date(2024, 9, 30), output_dir=tmp_path / "o")
    result = run_scan(cfg, FakeSource(frames), landcover_loader=_grass_landcover)
    assert result.candidates == []
    write_all(result)
    assert "No leads above the thresholds" in (tmp_path / "o" / "report.html").read_text()
