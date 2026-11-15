import pytest

from dgd import cli
from dgd.landcover import parse_classes, tile_names


def test_legacy_flags_route_to_classify(monkeypatch):
    seen = {}
    monkeypatch.setitem(cli.COMMANDS, "classify", lambda args: seen.setdefault("args", args) and 0)
    assert cli.main(["--before-path", "a.tif", "--after-path", "b.tif", "--no-progress"]) == 0
    assert seen["args"].command == "classify" and seen["args"].before_path == "a.tif"


def test_parse_args_compat_for_old_scripts():
    args = cli.parse_args(["--prob-threshold", "0.4"])
    assert args.prob_threshold == 0.4 and args.disturbed_class_id == 3


def test_scan_needs_exactly_one_aoi(monkeypatch):
    p = cli.build_parser()
    args = p.parse_args(["scan", "--lat", "41", "--lon", "14", "--bbox", "14,41,14.1,41.1"])
    with pytest.raises(SystemExit, match="exactly one"):
        cli._resolve_aoi(args)
    args = p.parse_args(["scan", "--lat", "41"])
    with pytest.raises(SystemExit, match="--lat needs --lon"):
        cli._resolve_aoi(args)
    grid, mask = cli._resolve_aoi(p.parse_args(["scan", "--bbox", "14.38,41.08,14.42,41.11"]))
    assert mask is None and grid.width > 0


def test_bad_values_are_rejected():
    p = cli.build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["scan", "--lat", "1", "--lon", "2", "--start", "yesterday"])
    with pytest.raises(SystemExit):
        p.parse_args(["scan", "--bbox", "1,2,3"])


def test_errors_become_exit_code_one(monkeypatch):
    def boom(args):
        raise RuntimeError("Clouds won.")

    monkeypatch.setitem(cli.COMMANDS, "scan", boom)
    assert cli.main(["scan", "--lat", "1", "--lon", "2"]) == 1
    assert cli.main([]) == 2


def test_landcover_helpers():
    assert parse_classes("water, snow,built-up") == (50, 70, 80)
    assert parse_classes("40") == (40,)
    assert parse_classes("") == ()
    with pytest.raises(ValueError, match="Unknown land cover"):
        parse_classes("lava")
    assert tile_names((14.38, 41.08, 14.42, 41.11)) == ["N39E012"]
    assert tile_names((-1.3, 51.8, -1.2, 51.9)) == ["N51W003"]
    assert tile_names((-0.5, -0.5, 0.5, 0.5)) == ["S03W003", "S03E000", "N00W003", "N00E000"]


def test_legacy_download_flags_run_download_then_classify(monkeypatch):
    seen = []
    monkeypatch.setitem(cli.COMMANDS, "download", lambda args: seen.append(args) or 0)
    monkeypatch.setitem(cli.COMMANDS, "classify", lambda args: seen.append(args) or 0)
    rc = cli.main(["--download-sentinel", "--before-date", "2023-01-01", "--after-date", "2024-01-01", "--lat", "1",
                   "--long", "-2.5", "--cloud-pct", "30", "--before-path", "b.tif", "--no-progress"])
    assert rc == 0
    dl, cl = seen
    assert dl.command == "download" and (dl.lat, dl.lon, dl.radius_m, dl.max_cloud) == (1, -2.5, 1500, 30)
    assert str(dl.before_start) == "2023-01-01" and dl.window_days == 60
    assert dl.before_path == "b.tif" and dl.after_path == "after.tif"
    assert cl.command == "classify" and cl.before_path == "b.tif" and cl.no_progress


def test_legacy_download_without_place_says_what_is_missing():
    with pytest.raises(SystemExit, match="--long"):
        cli.main(["--download-sentinel", "--lat", "1", "--before-date", "2023-01-01", "--after-date", "2024-01-01"])


def test_scan_warns_about_baseline_years_before_the_archive(monkeypatch, caplog):
    seen = {}

    def fake_run(config, source, progress=None):
        seen["years"] = config.baseline_years
        raise RuntimeError("stop here")

    monkeypatch.setattr("dgd.scan.run_scan", fake_run)
    rc = cli.main(["scan", "--lat", "52.3", "--lon", "4.9", "--start", "2020-04-01", "--end", "2020-09-30", "--baseline-years", "6", "--no-progress", "--no-cache"])
    assert rc == 1 and seen["years"] == 6  # still runs, with the years that exist
    assert "before Sentinel-2 imagery" in caplog.text
    with pytest.raises(SystemExit, match="at least 1"):
        cli.main(["scan", "--lat", "52.3", "--lon", "4.9", "--baseline-years", "0", "--no-progress"])
