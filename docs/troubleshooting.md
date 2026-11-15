# Troubleshooting

## "No Sentinel-2 passes in the target window"

The archive has nothing for that place and time under the cloud limit. Widen the window, check the dates (`YYYY-MM-DD`, start before end), or raise `--max-cloud`. Sentinel-2 starts in mid 2015, with good coverage from 2017 and Level-2A from about 2018 in most places.

## "Only N usable target passes" or "Only N usable baseline passes"

Clouds won. Options, best first:

1. Widen the target window. Six months is the default for a reason.
2. Use more baseline years: `--baseline-years 4`.
3. Lower `--min-target-obs` or `--min-baseline-obs`. This trades reliability for coverage, so the leads get noisier.

In the wet tropics, pick a dry season window if you can.

## Hundreds of leads

Usually farmland. Try, in order:

- `--min-score 4`
- `--min-area-m2 800`
- `--exclude-landcover water,snow,cropland` (you will miss dumps on cropland, so know what you are giving up)
- `--no-transient`

Then look at the top of each list only. The ranking puts field-shaped and cropland leads lower for a reason.

## No leads at all

Either the area is clean or the thresholds are too strict. Both happen. Try `--min-score 2.5 --min-area-m2 200` and see what crawls out. Check the report header: if there are only a handful of target passes, the scan was half blind.

## A known dump was not found

Check, in this order:

1. Was it there before the baseline years? Then it is scenery, not change. Move the target window to when it appeared.
2. Is it smaller than about 300 m2, or under trees? Then Sentinel-2 cannot see it.
3. Did it appear in the last few weeks of the window? Then there may not be enough clear passes after it. Extend `--end`.
4. Look at `score.tif` at that spot in QGIS. If it sits just under the threshold, lower `--min-score`.

## The download is slow

The first scan of an area downloads every clear pass for four seasons. A 3 x 3 km area takes 5 to 10 minutes. Raise `--workers` on a good connection. Reruns come from `~/.cache/dgd` and take seconds. Delete that folder to reclaim disk.

## Network errors

Failed passes are retried, then skipped and listed under `failed_fetches` in `manifest.json`. A few skips do not matter. If most fail, check that `https://earth-search.aws.element84.com/v1` is reachable from your network. Some government networks block AWS. Ask your IT people for an exception or run from outside.

## classify: "No such file or directory: training_polygons.gpkg"

Generate a placeholder and edit it, see [classify mode](classify.md).

## classify: "validation on random pixels (OPTIMISTIC, neighbors leak)" in metrics.txt

At least one class has a single polygon. Draw at least two per class and validation switches to held-out polygons.

## Earth Engine

`dgd download --source ee` needs `pip install -e ".[ee]"`, a one-time `earthengine authenticate`, and your own project id via `--ee-project` or `EE_PROJECT`. The default `--source stac` needs none of that.

## graves: "OpenStreetMap unavailable"

The public Overpass servers are run by volunteers and are sometimes overloaded or down. The search carries on without building and road masks and without the access ranking, and says so in the report. To stop depending on them, download a free extract of your country or province from [Geofabrik](https://download.geofabrik.de/) and pass `--osm-file region.osm.pbf`. Reading the area out of a province file takes about half a minute. If your organization runs its own Overpass server, use `--overpass-url`.
