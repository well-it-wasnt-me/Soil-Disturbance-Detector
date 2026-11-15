"""Command line interface.

Three commands:

``scan``      find new dumping, burning and digging in an area. No labels needed.
``classify``  the original supervised mode: your before/after rasters plus your
              labeled polygons, RandomForest does the rest.
``download``  save before/after Sentinel-2 composites as GeoTIFFs, for
              ``classify`` or for looking at in QGIS.
``graves``    search drone, aerial or satellite imagery at 3 cm to 1 m for
              grave-sized disturbed ground.
``ui``        a map in the browser: draw a box, run any of the above.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dgd import __version__

log = logging.getLogger("dgd")


def _date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{text}' is not a YYYY-MM-DD date")


def _bbox(text: str) -> tuple[float, float, float, float]:
    try:
        vals = tuple(float(v) for v in text.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("bbox must be four numbers: min_lon,min_lat,max_lon,max_lat")
    if len(vals) != 4:
        raise argparse.ArgumentTypeError("bbox must be four numbers: min_lon,min_lat,max_lon,max_lat")
    return vals  # type: ignore[return-value]


# Settings that change with what you are looking for. Explicit flags always win.
SCAN_PRESETS = {
    "dumping": {"min_area_m2": 300.0, "min_event_days": 14, "min_event_score": 4.0, "window_days": 182},
    # Someone digging a grave with a machine leaves a few hundred square metres of
    # bare soil, tracks and spoil, for days to weeks. Hand-dug single graves are
    # invisible at 10 m and no preset changes that. This narrows the search for
    # the bigger stuff and points the drone at where to fly.
    "burial": {"min_area_m2": 200.0, "min_event_days": 10, "min_event_score": 3.5, "window_days": 150},
}


FETCH_SOURCES = ("naip", "pdok", "ign-fr", "pnoa-es", "nrw-de", "bayern-de", "linz-nz", "oam")


def default_cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "dgd"


def _add_aoi_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("where (pick one)")
    g.add_argument("--lat", type=float, help="Latitude of the centre, decimal degrees.")
    g.add_argument("--lon", "--long", dest="lon", type=float, help="Longitude of the centre, decimal degrees.")
    g.add_argument("--radius-m", type=float, default=1500, help="Half-width of the square around lat/lon, metres. Default 1500.")
    g.add_argument("--bbox", type=_bbox, help="min_lon,min_lat,max_lon,max_lat instead of a point.")
    g.add_argument("--aoi", help="Polygon file (GeoJSON, GPKG, Shapefile). Only pixels inside it are reported.")


def _resolve_aoi(args):
    from dgd.aoi import grid_from_bbox, grid_from_point, grid_from_vector

    given = sum(x is not None for x in (args.lat, args.bbox, args.aoi))
    if given != 1:
        raise SystemExit("Give exactly one of --lat/--lon, --bbox or --aoi.")
    if args.aoi:
        return grid_from_vector(args.aoi)
    if args.bbox:
        return grid_from_bbox(*args.bbox), None
    if args.lon is None:
        raise SystemExit("--lat needs --lon.")
    return grid_from_point(args.lat, args.lon, args.radius_m), None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dgd",
        description="Find ground that changed when it should not have: illegal dumping, waste burning, digging.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="More log output.")
    sub = parser.add_subparsers(dest="command")

    # scan
    s = sub.add_parser("scan", help="Unsupervised scan of an area for new dumping, burning and digging.",
                       description="Compare a target window with the same season in previous years and report what changed.")
    _add_aoi_args(s)
    s.add_argument("--preset", choices=sorted(SCAN_PRESETS), default="dumping",
                   help="dumping (default): surface dumps and burning. burial: digging after a disappearance date, "
                        "smaller leads, shorter events, use with --since.")
    w = s.add_argument_group("when")
    w.add_argument("--since", type=_date, help="For --preset burial: the date the person went missing. Sets --start.")
    w.add_argument("--start", type=_date, help="Target window start, YYYY-MM-DD. Default: 6 months before --end.")
    w.add_argument("--end", type=_date, help="Target window end, YYYY-MM-DD. Default: today.")
    w.add_argument("--baseline-years", type=int, default=3,
                   help="Prior years used as 'normal'. Default 3. More years make 'normal' sturdier, as far back as "
                        "the archive goes: November 2016 at best, 2018 in many places.")
    t = s.add_argument_group("thresholds")
    t.add_argument("--min-score", type=float, default=3.0, help="Persistent change score threshold (robust z). Default 3.")
    t.add_argument("--min-event-score", type=float, help="Transient dig event threshold (robust z). Default 4, burial 3.5.")
    t.add_argument("--min-persistence", type=float, default=0.5, help="Share of clear passes that must agree. Default 0.5.")
    t.add_argument("--min-event-days", type=int, help="Minimum duration of a change, days. Default 14, burial 10.")
    t.add_argument("--min-area-m2", type=float, help="Smallest lead worth reporting, square metres. Default 300, burial 200.")
    t.add_argument("--exclude-landcover", default="water,snow",
                   help="WorldCover classes to ignore, comma separated (tree, shrub, grass, cropland, built-up, bare, "
                        "snow, water, wetland, mangrove, moss). Default: water,snow. Use 'none' to keep everything.")
    t.add_argument("--no-transient", action="store_true", help="Skip dig-and-cover event detection.")
    t.add_argument("--no-regional-correction", action="store_true", help="Do not remove area-wide drift (droughts, haze).")
    t.add_argument("--min-baseline-obs", type=int, default=6, help="Fewest clear baseline looks per pixel. Default 6.")
    t.add_argument("--min-target-obs", type=int, default=3, help="Fewest clear target looks per pixel. Default 3.")
    o = s.add_argument_group("data and output")
    o.add_argument("--output-dir", help="Where results go. Default: outputs/scan_<timestamp>.")
    o.add_argument("--name", default="", help="Human name for the area, shown in the report.")
    o.add_argument("--max-cloud", type=float, default=70, help="Skip granules cloudier than this percent. Default 70.")
    o.add_argument("--cache-dir", default=str(default_cache_dir()), help="Download cache. Default: ~/.cache/dgd.")
    o.add_argument("--no-cache", action="store_true", help="Do not cache downloads.")
    o.add_argument("--workers", type=int, default=8, help="Parallel downloads. Default 8.")
    o.add_argument("--max-passes-per-fortnight", type=int, default=5,
                   help="Keep only the N clearest passes per 14 days. Saves downloads where orbits overlap. 0 keeps all. Default 5.")
    o.add_argument("--max-chips", type=int, default=30, help="Image clips for the top N leads. Default 30.")
    o.add_argument("--no-progress", action="store_true", help="No progress bars.")
    o.add_argument("--progress-json", action="store_true", help="Progress as JSON lines on stdout, for the map UI.")

    # classify
    c = sub.add_parser("classify", help="Supervised before/after classification with your own labeled polygons.")
    c.add_argument("--before-path", default="before.tif", help="Before raster.")
    c.add_argument("--after-path", default="after.tif", help="After raster.")
    c.add_argument("--training-path", default="training_polygons.gpkg", help="Polygons with a class_id column.")
    c.add_argument("--output-dir", default="outputs", help="Where results go.")
    c.add_argument("--disturbed-class-id", type=int, default=3, help="class_id that means disturbed. Default 3.")
    c.add_argument("--min-patch-area-m2", type=float, default=400, help="Drop polygons smaller than this. Default 400.")
    c.add_argument("--prob-threshold", type=float, default=0.60, help="Probability cutoff for the binary mask. Default 0.6.")
    c.add_argument("--red-band-index", type=int, default=2, help="Zero-based red band. Default 2.")
    c.add_argument("--nir-band-index", type=int, default=3, help="Zero-based NIR band. Default 3.")
    c.add_argument("--no-progress", action="store_true", help="No progress bars.")

    # graves
    g = sub.add_parser("graves", help="Search high resolution imagery (drone, aerial, VHR) for grave-sized ground disturbance.",
                       description="Search drone, aerial or satellite imagery at 3 cm to 1 m for grave-sized disturbed ground. "
                                   "Candidates for a search, not findings.")
    gi = g.add_argument_group("imagery (your own files, or --fetch)")
    gi.add_argument("--after", help="Image to search (GeoTIFF). Usually the most recent drone or aerial survey.")
    gi.add_argument("--before", help="Optional earlier image of the same place, for change detection.")
    gi.add_argument("--dsm", help="Optional surface model (GeoTIFF, metres) from the same survey as --after.")
    gi.add_argument("--thermal", help="Optional thermal image (GeoTIFF, one band) from the same survey. Best for graves days to weeks old.")
    gi.add_argument("--bands", help="Band order of --after: rgb, bgr, rgbn, bgrn, nrg, sequoia, m3m, p4m, micasense, "
                                    "or a comma list like green,red,rededge,nir. Default: guess from band count.")
    gi.add_argument("--before-bands", help="Band order of --before. Default: guess.")
    gi.add_argument("--after-date", help="Date of --after, for the report.")
    gi.add_argument("--before-date", help="Date of --before, for the report.")
    gf = g.add_argument_group("free imagery instead of files")
    gf.add_argument("--fetch", choices=FETCH_SOURCES,
                    help="Free imagery source: " + ", ".join(FETCH_SOURCES) + ". Run 'dgd imagery --lat .. --lon ..' to see what exists where.")
    gf.add_argument("--lat", type=float, help="Centre latitude for --fetch.")
    gf.add_argument("--lon", type=float, help="Centre longitude for --fetch.")
    gf.add_argument("--radius-m", type=float, default=100, help="Half-width of the area for --fetch, metres. Default 100.")
    gf.add_argument("--after-year", help="Year to search, or 'latest', for --fetch.")
    gf.add_argument("--before-year", help="Earlier year for change detection, for --fetch.")
    gs = g.add_argument_group("search")
    gs.add_argument("--aoi", help="Polygon file limiting the search area.")
    gs.add_argument("--mask-out", help="Polygon file of places to skip: buildings, ponds, areas already searched.")
    gs.add_argument("--work-res", type=float, default=0.1, help="Working pixel size, metres. Finer imagery is averaged down. Default 0.1.")
    gs.add_argument("--min-score", type=float, default=3.0, help="Evidence threshold, robust z. Default 3.")
    gs.add_argument("--mass", action="store_true", help="Look for mass graves and trenches instead of single graves.")
    gs.add_argument("--no-osm", action="store_true", help="Do not rank by distance to roads and tracks from OpenStreetMap.")
    gs.add_argument("--osm-file", help="Local OpenStreetMap extract (.osm.pbf, e.g. from Geofabrik). Offline, no server needed.")
    gs.add_argument("--overpass-url", help="Your own Overpass server, tried before the public ones. Also DGD_OVERPASS_URL.")
    go = g.add_argument_group("output")
    go.add_argument("--output-dir", help="Default: outputs/graves_<timestamp>.")
    go.add_argument("--name", default="", help="Case or area name for the report.")
    go.add_argument("--max-chips", type=int, default=30, help="Close-up clips for the top N candidates. Default 30.")
    go.add_argument("--cache-dir", default=str(default_cache_dir()), help="Cache for OpenStreetMap queries.")
    go.add_argument("--no-progress", action="store_true", help="No progress bars.")
    go.add_argument("--progress-json", action="store_true", help="Progress as JSON lines on stdout, for the map UI.")

    # imagery
    im = sub.add_parser("imagery", help="List free high resolution imagery (sources and years) available at a place.")
    im.add_argument("--lat", type=float, required=True, help="Latitude.")
    im.add_argument("--lon", type=float, required=True, help="Longitude.")
    im.add_argument("--radius-m", type=float, default=100, help="Half-width of the area, metres. Default 100.")
    im.add_argument("--cache-dir", default=str(default_cache_dir()), help="Cache (the New Zealand index lives here).")

    # ui
    u = sub.add_parser("ui", help="Open a map in the browser, draw a box, run scans. Runs locally.")
    u.add_argument("--port", type=int, default=8765, help="Local port. Default 8765.")
    u.add_argument("--output-dir", default="outputs/ui", help="Where job results go. Default outputs/ui.")
    u.add_argument("--cache-dir", default=str(default_cache_dir()), help="Download cache. Default ~/.cache/dgd.")
    u.add_argument("--no-browser", action="store_true", help="Do not open a browser window.")

    # download
    d = sub.add_parser("download", help="Save before/after Sentinel-2 median composites as GeoTIFFs.")
    _add_aoi_args(d)
    d.add_argument("--before-start", type=_date, required=True, help="Before window start, YYYY-MM-DD.")
    d.add_argument("--before-end", type=_date, help="Before window end. Default: start + --window-days.")
    d.add_argument("--after-start", type=_date, required=True, help="After window start, YYYY-MM-DD.")
    d.add_argument("--after-end", type=_date, help="After window end. Default: start + --window-days.")
    d.add_argument("--window-days", type=int, default=60, help="Window length when no end is given. Default 60.")
    d.add_argument("--before-path", default="before.tif", help="Output path for the before composite.")
    d.add_argument("--after-path", default="after.tif", help="Output path for the after composite.")
    d.add_argument("--source", choices=("stac", "ee"), default="stac",
                   help="stac: free Element84 Earth Search, no account (default). ee: Google Earth Engine.")
    d.add_argument("--ee-project", default=os.environ.get("EE_PROJECT"), help="Earth Engine project id (or EE_PROJECT env var).")
    d.add_argument("--max-cloud", type=float, default=70, help="Skip granules cloudier than this percent. Default 70.")
    d.add_argument("--cache-dir", default=str(default_cache_dir()), help="Download cache.")
    d.add_argument("--workers", type=int, default=8, help="Parallel downloads.")
    return parser


def _progress(args, steps: int, label: str):
    from dgd.progress import JsonProgress, NullProgress, TqdmProgress

    if getattr(args, "progress_json", False):
        return JsonProgress(total_steps=steps)
    return NullProgress() if args.no_progress else TqdmProgress(total_steps=steps, label=label)


def cmd_scan(args, progress=None) -> int:
    from dgd.landcover import parse_classes
    from dgd.report import write_all
    from dgd.scan import ScanConfig, run_scan
    from dgd.stac import EarthSearchSource

    grid, mask = _resolve_aoi(args)
    preset = SCAN_PRESETS[args.preset]
    for key in ("min_area_m2", "min_event_days", "min_event_score"):
        if getattr(args, key) is None:
            setattr(args, key, preset[key])
    today = datetime.now(timezone.utc).date()
    if args.since:
        if args.start and args.start != args.since:
            raise SystemExit("Give --since or --start, not both.")
        args.start = args.since
        if args.preset != "burial":
            log.info("--since given: you probably want --preset burial as well.")
    if args.start and not args.end:
        end = min(today, args.start + timedelta(days=preset["window_days"]))
    else:
        end = args.end or today
    start = args.start or (end - timedelta(days=preset["window_days"]))
    if args.baseline_years < 1:
        raise SystemExit("--baseline-years must be at least 1.")
    from dgd.anomaly import shift_years
    from dgd.stac import S2_FIRST, S2_GOOD

    oldest = shift_years(start, -args.baseline_years)
    if oldest < S2_FIRST:
        log.warning("The oldest baseline year starts %s, before Sentinel-2 imagery on Earth Search (November 2016). "
                    "Those years add nothing; the scan uses the ones that have imagery.", oldest)
    elif oldest < S2_GOOD:
        log.info("Baseline years before 2018 have thinner Sentinel-2 coverage, and none in parts of the world.")
    excl = () if args.exclude_landcover.strip().lower() == "none" else parse_classes(args.exclude_landcover)
    out = Path(args.output_dir or f"outputs/scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    cache = None if args.no_cache else Path(args.cache_dir).expanduser()
    npx = grid.width * grid.height
    if npx > 4_000_000:
        log.warning("Area is %d x %d pixels. That works, but it will take a while and want a few GB of disk cache.", grid.width, grid.height)

    config = ScanConfig(
        grid=grid,
        target_start=start,
        target_end=end,
        output_dir=out,
        baseline_years=args.baseline_years,
        min_score=args.min_score,
        min_event_score=args.min_event_score,
        min_persistence=args.min_persistence,
        min_event_span_days=args.min_event_days,
        min_area_m2=args.min_area_m2,
        min_baseline_obs=args.min_baseline_obs,
        min_target_obs=args.min_target_obs,
        exclude_landcover=excl,
        aoi_mask=mask,
        max_chips=args.max_chips,
        max_passes_per_fortnight=args.max_passes_per_fortnight,
        regional_normalization=not args.no_regional_correction,
        detect_transient=not args.no_transient,
        cache_dir=cache,
        name=args.name,
    )
    source = EarthSearchSource(cache_dir=cache, max_cloud=args.max_cloud, workers=args.workers)
    if progress is None:
        progress = _progress(args, 7, "Scan")
    try:
        result = run_scan(config, source, progress=progress)
        progress.step("Writing report")
        outputs = write_all(result)
    finally:
        progress.close()
    n_p = sum(c.kind == "persistent" for c in result.candidates)
    n_t = len(result.candidates) - n_p
    print(f"{n_p} persistent and {n_t} transient leads. Open {outputs['report.html']}")
    return 0


def cmd_classify(args) -> int:
    from dgd.pipeline import DisturbanceDetector
    from dgd.progress import NullProgress, TqdmProgress

    progress = NullProgress() if args.no_progress else TqdmProgress(total_steps=DisturbanceDetector.STEPS)
    DisturbanceDetector(args, progress=progress).run()
    return 0


def cmd_download(args) -> int:
    from dgd.composite import download_composites

    grid, _ = _resolve_aoi(args)
    span = timedelta(days=args.window_days)
    windows = {
        "before": (args.before_start, args.before_end or args.before_start + span),
        "after": (args.after_start, args.after_end or args.after_start + span),
    }
    paths = {"before": Path(args.before_path), "after": Path(args.after_path)}
    if args.source == "ee":
        from dgd.sentinel import download_sentinel_pair_ee

        download_sentinel_pair_ee(grid, windows, paths, project=args.ee_project, max_cloud=args.max_cloud)
    else:
        from dgd.stac import EarthSearchSource

        source = EarthSearchSource(cache_dir=Path(args.cache_dir).expanduser(), max_cloud=args.max_cloud, workers=args.workers)
        download_composites(grid, windows, paths, source)
    for k, p in paths.items():
        print(f"Saved {k} composite to {p}")
    return 0


def _bbox_around(lat: float, lon: float, radius_m: float) -> tuple[float, float, float, float]:
    import math

    dlat = radius_m / 111320
    dlon = radius_m / (111320 * max(0.01, math.cos(math.radians(lat))))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def cmd_graves(args, progress=None) -> int:
    from dgd.grave_report import write_all
    from dgd.grave_search import GraveSearchConfig, search

    out = Path(args.output_dir or f"outputs/graves_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    after, before = args.after, args.before
    after_date, before_date = args.after_date, args.before_date
    bands, before_bands = args.bands, args.before_bands
    sources = []
    if args.fetch:
        if args.lat is None or args.lon is None or args.after_year is None:
            raise SystemExit("--fetch needs --lat, --lon and --after-year.")
        from dgd import highres_sources as hs

        src = hs.SOURCES[args.fetch]
        bbox = _bbox_around(args.lat, args.lon, args.radius_m)
        if not src.covers(bbox):
            raise SystemExit(f"{src.name} does not cover this place. Try 'dgd imagery --lat {args.lat} --lon {args.lon}'.")
        inputs = out / "inputs"
        cache = Path(args.cache_dir).expanduser()
        after_year = hs.parse_year(args.after_year)
        after = str(src.fetch(bbox, after_year, inputs / f"after_{args.fetch}_{after_year}.tif", cache))
        after_date = str(after_year)
        sources.append(src.citation)
        if args.before_year:
            before_year = hs.parse_year(args.before_year)
            before = str(src.fetch(bbox, before_year, inputs / f"before_{args.fetch}_{before_year}.tif", cache))
            before_date = str(before_year)
            if src.citation not in sources:
                sources.append(src.citation)
        # Fetched files say what their bands are. Some years have infrared and
        # some do not, so let the reader count bands instead of assuming.
        bands = before_bands = None
    if not after:
        raise SystemExit("Give --after IMAGE, or --fetch SOURCE with --lat, --lon and --after-year.")
    if not args.no_osm:
        from dgd.osm import OSM_CITATION

        sources.append(OSM_CITATION)
        if args.overpass_url:
            os.environ["DGD_OVERPASS_URL"] = args.overpass_url
    cfg = GraveSearchConfig(
        after_path=after, before_path=before, dsm_path=args.dsm, thermal_path=args.thermal, bands=bands, before_bands=before_bands,
        aoi_path=args.aoi, mask_out_path=args.mask_out, work_res=args.work_res, min_score=args.min_score, mass=args.mass, use_osm=not args.no_osm, osm_file=args.osm_file,
        cache_dir=Path(args.cache_dir).expanduser(), output_dir=out, name=args.name, max_chips=args.max_chips,
        after_date=after_date, before_date=before_date, sources=sources,
    )
    if progress is None:
        progress = _progress(args, 6, "Grave search")
    try:
        result = search(cfg, progress=progress)
        progress.step("Writing report")
        outputs = write_all(result)
    finally:
        progress.close()
    print(f"{len(result.candidates)} candidates. Open {outputs['report.html']}")
    print("Do not dig. Mark, protect, photograph, and call the forensic team.")
    return 0


def cmd_imagery(args) -> int:
    from dgd import highres_sources as hs

    bbox = _bbox_around(args.lat, args.lon, args.radius_m)
    cache = Path(args.cache_dir).expanduser()
    found = False
    for src in hs.sources_for(bbox):
        try:
            years = src.available(bbox, cache)
        except Exception as exc:
            print(f"{src.key:10s} {src.name}: could not ask ({exc})")
            continue
        if not years:
            continue
        found = True
        print(f"{src.key:10s} {src.name}, {src.license}")
        for a in years:
            print(f"           {str(a.year):7s} {a.res_m * 100:4.0f} cm  {'NIR' if a.nir else '   '}  {a.label if a.label != str(a.year) else ''}")
    if not found:
        print("No free high resolution imagery found here. Drone imagery, or a commercial scene bought by your agency, "
              "goes in with 'dgd graves --after FILE'.")
    return 0


def cmd_ui(args) -> int:
    from dgd.ui.server import serve

    serve(Path(args.output_dir), str(Path(args.cache_dir).expanduser()), port=args.port, open_browser=not args.no_browser)
    return 0


COMMANDS = {"scan": cmd_scan, "classify": cmd_classify, "download": cmd_download, "graves": cmd_graves,
            "imagery": cmd_imagery, "ui": cmd_ui}


def _legacy_commands(argv: list[str]) -> list[list[str]]:
    """Turn a 0.1 call (one flat command, no subcommand) into ``download`` and/or ``classify``.

    0.1 downloaded with ``--download-sentinel --lat --long --before-date
    --after-date`` and then classified, all in one go. Those flags belong to
    ``download`` now. Without ``--download-sentinel`` 0.1 ignored them, so
    they are dropped here too.
    """
    lp = argparse.ArgumentParser(prog="dgd", add_help=False)
    lp.add_argument("--download-sentinel", action="store_true")
    lp.add_argument("--before-date", type=_date)
    lp.add_argument("--after-date", type=_date)
    lp.add_argument("--lat")
    lp.add_argument("--long", "--lon", dest="lon")
    lp.add_argument("--buffer-m", default="1500")
    lp.add_argument("--window-days", default="60")
    lp.add_argument("--cloud-pct")
    lp.add_argument("--scale", type=float)
    old, rest = lp.parse_known_args(argv)
    classify = ["classify", *rest]
    if not old.download_sentinel:
        return [classify]
    missing = [f for f, v in (("--lat", old.lat), ("--long", old.lon), ("--before-date", old.before_date),
                              ("--after-date", old.after_date)) if v is None]
    if missing:
        raise SystemExit(f"--download-sentinel needs {', '.join(missing)}. Or use 'dgd download', see 'dgd download --help'.")
    if old.scale is not None and old.scale != 10:
        log.warning("--scale %g is ignored. Composites are always 10 m now.", old.scale)
    paths = argparse.ArgumentParser(add_help=False)
    paths.add_argument("--before-path", default="before.tif")
    paths.add_argument("--after-path", default="after.tif")
    p, _ = paths.parse_known_args(rest)
    download = ["download", "--lat", old.lat, "--lon", old.lon, "--radius-m", old.buffer_m,
                "--before-start", old.before_date.isoformat(), "--after-start", old.after_date.isoformat(),
                "--window-days", old.window_days, "--before-path", p.before_path, "--after-path", p.after_path]
    if old.cloud_pct is not None:
        download += ["--max-cloud", old.cloud_pct]
    log.info("0.1 style call: running 'dgd %s' then 'dgd %s'", " ".join(download[1:]), " ".join(classify[1:]))
    return [download, classify]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # pystac complains about every Earth Search asset href it cannot parse
    # into a bucket name. It is right, and nobody cares.
    warnings.filterwarnings("ignore", message="Could not parse bucket", category=UserWarning)
    # The first release had no subcommands. Old muscle memory still works.
    if argv and argv[0].startswith("--") and argv[0] not in ("--help", "--version", "--verbose"):
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
        for step in _legacy_commands(argv):
            rc = _run(step)
            if rc:
                return rc
        return 0
    return _run(argv)


def _run(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    for noisy in ("rasterio", "pystac_client", "urllib3", "matplotlib", "pyogrio", "fiona"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if not args.command:
        parser.print_help()
        return 2
    try:
        return COMMANDS[args.command](args)
    except (RuntimeError, ValueError, FileNotFoundError) as exc:
        log.error("%s", exc)
        return 1


def parse_args(argv: list[str] | None = None):
    """Parse arguments for ``classify``. Kept for scripts written against 0.1."""
    return build_parser().parse_args(["classify", *(sys.argv[1:] if argv is None else argv)])


__all__ = ["build_parser", "main", "parse_args"]
