# Soil-Disturbance-Detector

Free software that watches free satellite imagery for ground that changed when it had no business changing: illegal dumping, waste burning, and holes that got dug and filled back in. And, at drone scale, for the kind of hole nobody wants found. Built to hand law enforcement a short list of places worth a look. Costs nothing, needs no account, and nobody is selling you a subscription at the end of this README.

It produces **leads, not evidence**. A lead is a patch of ground that stopped behaving like it did in the same season of previous years. Farmers, builders and the weather produce the same signals as criminals do. Every lead needs a human, better imagery or a pair of boots before anybody acts on it.

## What it finds, and what it does not

| Target | Can Sentinel-2 see it? | Mode |
| --- | --- | --- |
| Surface dumps, a few hundred square metres and up | Yes. That is the main event. | `persistent` leads |
| Waste fires and burn scars | Yes | `persistent`, signature `burn` |
| Dig and cover: a pit dug, filled, grassed over | Often, if the bare soil sat there for two weeks or more | `transient` leads |
| Dumps that were already there before the baseline years | No. To a change detector they are scenery. | Planned |
| Old burials under regrown vegetation | Not yet. Needs multi-year stress analysis. | Planned |
| Machine-dug pits, mass graves, fresh tracks after a disappearance | Often | `dgd scan --preset burial --since <date>` |
| A single clandestine grave | Not from space. It is 2% of one 10 m pixel. Physics does not negotiate. | `dgd graves` on drone or aerial imagery |

## Quick start

```bash
git clone <this repo> && cd Soil-Disturbance-Detector
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

dgd scan --lat 51.824 --lon -1.266 --radius-m 1500 \
         --start 2025-04-01 --end 2025-09-30 \
         --name "Kidlington, Oxfordshire" --output-dir outputs/kidlington
```

Then open `outputs/kidlington/report.html`. Prefer a map? `dgd ui` opens one in your browser: drag a box anywhere on Earth and run any of the scans from there ([docs/ui.md](docs/ui.md)). First run downloads a few hundred megabytes of imagery and takes a few minutes. Reruns on the same area come from the cache and take seconds.

That example is not random. In 2025 roughly 20,000 tonnes of waste were dumped on a field between the A34 and the River Cherwell near Kidlington, and the Environment Agency shut it down in October. The scanner, told nothing except "look here, April to September", puts the dump in its top three leads and dates its arrival to spring 2025, months before the Environment Agency first went on site that July. See [docs/evaluation.md](docs/evaluation.md) for that and the broader test.

## Searching for a grave

A different tool for a different scale. `dgd graves` takes drone, aerial or satellite imagery at 3 cm to 1 m and ranks grave-sized patches of disturbed ground for a search team, with GPS waypoints:

```bash
dgd graves --after drone_ortho.tif --aoi search_area.geojson --name "Case 114" --output-dir outputs/case_114
dgd graves --fetch pdok --lat 52.11 --lon 4.88 --radius-m 150 --after-year 2026 --before-year 2025   # free 8 cm, Netherlands
```

Free national aerial photos are fetched automatically for the USA, the Netherlands, France, Spain, North Rhine-Westphalia, Bavaria and New Zealand, plus OpenAerialMap patches anywhere. `dgd imagery --lat .. --lon ..` lists what exists at a place. Fresh graves in grass stand out. Old ones, carefully covered ones and ones in plowed soil are hard for any camera, and the docs say how hard. It is a search aid, not a verdict, and it prints the rule that matters most on every report: **do not dig, protect the scene, call the forensic team.** See [docs/graves.md](docs/graves.md).

## How it works, in one paragraph

For every 10 m pixel it looks up the same months in the previous three years and learns what normal looks like there, including how much normal wobbles. Then it measures how far the target window strays from that, in units of the pixel's own wobble. A field that gets plowed every April wobbles every April, so plowing it again is not news. A meadow that stayed a meadow for three summers and is now a white heap is news. Drought and haze that hit the whole area at once get subtracted out. Changes that show up and stay become `persistent` leads. Bare soil that shows up for at least two weeks and then heals becomes a `transient` lead, which is what a backfilled pit looks like from space. Details in [docs/how-it-works.md](docs/how-it-works.md).

## What you get

```
outputs/<scan>/
  report.html          open this first
  overview.png         the area with every lead outlined
  chips/P001.png       before, after, change score and score history per lead
  candidates.csv       one row per lead, with a Google Maps link
  candidates.kml       open in Google Earth or on a phone
  candidates.geojson   for QGIS and friends
  score.tif ...        the rasters behind it
  manifest.json        every scene, parameter and file hash that made this report
```

The manifest exists because if a lead ever turns into a case, somebody is going to ask how it was found. The answer should be on paper, not in somebody's memory.

## Data

- Imagery: Copernicus Sentinel-2 Level-2A, ESA Collection 1, served free by [Element84 Earth Search](https://earth-search.aws.element84.com/v1). No account, no key. Contains modified Copernicus Sentinel data.
- Land cover: ESA WorldCover 2021 v200 (CC BY 4.0).
- Google Earth Engine is supported as an optional download backend for `dgd download --source ee`, with your own project id.

## Documentation

- [Quickstart](docs/quickstart.md): install, first scan, reading the report
- [Map interface](docs/ui.md): `dgd ui`, draw a box, run scans, manage jobs
- [Field guide](docs/field-guide.md): how to triage leads and what fools the scanner
- [How it works](docs/how-it-works.md): the method, and its limits
- [Evaluation](docs/evaluation.md): how well it does on known sites
- [CLI reference](docs/cli.md)
- [Grave search](docs/graves.md): drone and aerial imagery, single graves, flying for it
- [Classify mode](docs/classify.md): the supervised mode, for when you already have labeled sites
- [Troubleshooting](docs/troubleshooting.md)
- [Roadmap](docs/roadmap.md): graves, old burials, and what else is coming

## Tests

```bash
pytest               # fast, offline, synthetic data
pytest -m network    # also talks to Earth Search for real
```

## License

CC0 1.0. Public domain. Use it, copy it, ship it to every police force on the planet. No permission needed and no thanks expected.
