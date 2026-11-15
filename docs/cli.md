# CLI reference

Installed as `dgd`. From a checkout without installing, `python main.py` does the same thing.

```
dgd scan       find new dumping, burning and digging in an area
dgd graves     search drone or aerial imagery for grave-sized disturbed ground
dgd classify   supervised before/after classification with your own labels
dgd download   save before/after Sentinel-2 composites as GeoTIFFs
dgd imagery    list free high resolution imagery at a place
dgd ui         a map in the browser for all of the above
dgd --version
```

Add `-v` before the command for debug logging.

## dgd scan

### Where (exactly one)

| Argument | Default | Meaning |
| --- | --- | --- |
| `--lat`, `--lon` | | Centre point, decimal degrees. Latitude first. |
| `--radius-m` | `1500` | Half-width of the square around the point, metres. |
| `--bbox` | | `min_lon,min_lat,max_lon,max_lat` |
| `--aoi` | | Polygon file (GeoJSON, GPKG, Shapefile). Only pixels inside it get reported. |

### Preset

| Argument | Default | Meaning |
| --- | --- | --- |
| `--preset` | `dumping` | `dumping`: surface dumps and burning. `burial`: digging after a disappearance, with minimum area 200 m2, events of 10 days, event threshold 3.5, a 150 day window. Explicit flags override the preset. |

### When

| Argument | Default | Meaning |
| --- | --- | --- |
| `--since` | | For `--preset burial`: the date the person went missing. Sets `--start`, and `--end` defaults to 150 days later or today. |
| `--start` | 6 months before `--end` | Target window start, `YYYY-MM-DD`. |
| `--end` | today | Target window end. Window can be at most a year. |
| `--baseline-years` | `3` | How many previous years define "normal" for this season. |

### Thresholds

| Argument | Default | Meaning |
| --- | --- | --- |
| `--min-score` | `3.0` | Persistent change score, robust z. Higher means fewer, stronger leads. |
| `--min-event-score` | `4.0` (burial `3.5`) | Transient dig event threshold, robust z. |
| `--min-persistence` | `0.5` | Share of clear passes after the change that must agree. |
| `--min-event-days` | `14` (burial `10`) | How long a change has to last to count. |
| `--min-area-m2` | `300` (burial `200`) | Smallest lead worth reporting. |
| `--exclude-landcover` | `water,snow` | WorldCover classes to ignore: `tree`, `shrub`, `grass`, `cropland`, `built-up`, `bare`, `snow`, `water`, `wetland`, `mangrove`, `moss`. Use `none` to keep everything. |
| `--no-transient` | off | Skip dig and cover detection. |
| `--no-regional-correction` | off | Do not subtract area-wide drift. Only useful for debugging. |
| `--min-baseline-obs` | `6` | Fewest clear baseline looks for a pixel to be scored. |
| `--min-target-obs` | `3` | Fewest clear target looks for a pixel to be scored. |

### Data and output

| Argument | Default | Meaning |
| --- | --- | --- |
| `--output-dir` | `outputs/scan_<timestamp>` | Where results go. |
| `--name` | | Name of the area, shown in the report. |
| `--max-cloud` | `70` | Skip granules cloudier than this. Pixel masking does the real work, so keep it generous. |
| `--cache-dir` | `~/.cache/dgd` | Download cache. |
| `--no-cache` | off | Do not cache. |
| `--workers` | `8` | Parallel downloads. |
| `--max-chips` | `30` | Image clips for the top N leads. |
| `--no-progress` | off | No progress bars. |

### Outputs

| File | What |
| --- | --- |
| `report.html` | Summary, lead table, overview map, image clips. Open this. |
| `overview.png` | The area with every lead outlined. |
| `chips/P001.png` | Before, after, score map and score timeline for one lead. |
| `candidates.csv` | One row per lead. Coordinates, area, scores, dates, Google Maps link. |
| `candidates.kml` | For Google Earth and phones. Red persistent, orange transient. |
| `candidates.geojson` | Lead outlines, EPSG:4326. |
| `score.tif` | Persistent change score. |
| `persistence.tif` | Share of clear passes above threshold after the change. |
| `event.tif` | Transient dig event strength. |
| `candidates_mask.tif` | Label raster matching the `label` field in the GeoJSON. |
| `manifest.json` | Version, command, every granule, every parameter, file hashes. |

### Exit codes

`0` success, `1` the scan could not run (no imagery, too cloudy, bad input, and the message says which), `2` no command given.

## dgd graves

Searches high resolution imagery for grave-sized disturbed ground. See [grave search](graves.md) for what it does and how to fly for it.

### Imagery

| Argument | Default | Meaning |
| --- | --- | --- |
| `--after` | | Image to search (GeoTIFF). Usually the newest drone or aerial survey. |
| `--before` | | Earlier image of the same place, for change detection. |
| `--dsm` | | Surface or ground model in metres from the same survey as `--after`. LiDAR ground models beat photogrammetry under vegetation. |
| `--thermal` | | Single-band thermal image from the same survey. Useful for graves days to weeks old. |
| `--bands`, `--before-bands` | guess | `rgb`, `bgr`, `rgbn`, `bgrn`, `nrg`, `sequoia`, `m3m`, `p4m`, `micasense`, or a comma list such as `blue,green,red,skip,nir`. |
| `--after-date`, `--before-date` | | Dates for the report. |

### Free imagery instead of files

| Argument | Default | Meaning |
| --- | --- | --- |
| `--fetch` | | Free imagery: `naip` (USA), `pdok` (Netherlands), `ign-fr` (France), `pnoa-es` (Spain), `nrw-de` (Germany, NRW), `bayern-de` (Germany, Bavaria), `linz-nz` (New Zealand), `oam` (OpenAerialMap, anywhere in patches). See [grave search](graves.md#free-high-resolution-imagery). Fetched images are saved under `<output>/inputs` and hashed in the manifest. |
| `--lat`, `--lon` | | Centre of the area. |
| `--radius-m` | `100` | Half-width of the area. |
| `--after-year`, `--before-year` | | Years to fetch, or `latest`. |

### Search

| Argument | Default | Meaning |
| --- | --- | --- |
| `--aoi` | | Polygon file limiting the search area. Use one. |
| `--mask-out` | | Polygon file of places to skip: buildings from a land registry, ponds, areas already searched on foot. Works with or without OpenStreetMap. |
| `--work-res` | `0.1` | Working pixel size, metres. Finer imagery is averaged down. |
| `--min-score` | `3.0` | Evidence threshold, robust z. |
| `--mass` | off | Look for mass graves and trenches (3 to 400 m long) instead of single graves. |
| `--no-osm` | off | No OpenStreetMap: no building and road mask, no access ranking. |
| `--osm-file` | | Local OpenStreetMap extract (`.osm.pbf`, for example from Geofabrik). Offline, no server needed. Use the smallest extract that covers the area. |
| `--overpass-url` | `$DGD_OVERPASS_URL` | Your own Overpass server, tried before the public ones. |

### Output

| Argument | Default | Meaning |
| --- | --- | --- |
| `--output-dir` | `outputs/graves_<timestamp>` | Where results go. |
| `--name` | | Case or area name. |
| `--max-chips` | `30` | Close-up clips for the top N candidates. |
| `--cache-dir` | `~/.cache/dgd` | Cache for OpenStreetMap queries. |

Outputs: `report.html`, `candidates.gpx` (waypoints for handheld GPS and phones), `candidates.kml`, `candidates.geojson`, `candidates.csv`, `evidence.tif`, `chips/G001.png`, `overview.png`, `manifest.json`.

## dgd imagery

Lists the free high resolution imagery available at a place, per source and year, with pixel size and whether near infrared exists. It asks the services, so the answer is about that exact spot.

```bash
dgd imagery --lat 47.877 --lon 1.945 [--radius-m 100]
```

## dgd ui

See [map interface](ui.md). Options: `--port` (default 8765, next free one if busy), `--output-dir` (default `outputs/ui`), `--cache-dir`, `--no-browser`.

## dgd classify

Supervised mode. See [classify mode](classify.md).

| Argument | Default | Meaning |
| --- | --- | --- |
| `--before-path` | `before.tif` | Pre-event raster. |
| `--after-path` | `after.tif` | Post-event raster, same grid as before. |
| `--training-path` | `training_polygons.gpkg` | Polygons with an integer `class_id` column. |
| `--output-dir` | `outputs` | Where results go. |
| `--disturbed-class-id` | `3` | The `class_id` that means disturbed. |
| `--prob-threshold` | `0.60` | Probability cutoff for the binary mask. |
| `--min-patch-area-m2` | `400` | Drop polygons smaller than this. |
| `--red-band-index` | `2` | Zero-based red band. |
| `--nir-band-index` | `3` | Zero-based NIR band. |
| `--no-progress` | off | No progress bars. |

Calls without a subcommand, like `python main.py --before-path a.tif`, still go to `classify`, for anyone with scripts from version 0.1.

## dgd download

Median composites for two windows, written as 6-band GeoTIFFs (blue, green, red, nir, swir16, swir22, reflectance 0 to 1). Band 2 is red and band 3 is NIR, which matches the `classify` defaults.

| Argument | Default | Meaning |
| --- | --- | --- |
| `--lat/--lon/--radius-m`, `--bbox`, `--aoi` | | Where, same as `scan`. |
| `--before-start` | required | Before window start. |
| `--before-end` | start + `--window-days` | Before window end. |
| `--after-start` | required | After window start. |
| `--after-end` | start + `--window-days` | After window end. |
| `--window-days` | `60` | Window length when no end is given. |
| `--before-path`, `--after-path` | `before.tif`, `after.tif` | Output files. |
| `--source` | `stac` | `stac` is free and needs no account. `ee` uses Google Earth Engine. |
| `--ee-project` | `$EE_PROJECT` | Your Earth Engine project id, for `--source ee`. |
| `--max-cloud`, `--cache-dir`, `--workers` | | As for `scan`. |

Earth Engine needs `pip install -e ".[ee]"` and a one-time `earthengine authenticate`.
