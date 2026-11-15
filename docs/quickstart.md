# Quickstart

## 1. Install

Python 3.11 or newer.

```bash
git clone <this repo>
cd Soil-Disturbance-Detector
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
dgd --version
```

No accounts, no API keys, no credit card. The imagery comes from a public archive.

## 2. Scan an area

Pick a place and a time window. The window is the period you suspect something happened in. Six months is a good default.

```bash
dgd scan --lat 51.824 --lon -1.266 --radius-m 1500 \
         --start 2025-04-01 --end 2025-09-30 \
         --name "Kidlington, Oxfordshire" \
         --output-dir outputs/kidlington
```

Other ways to say where:

```bash
dgd scan --bbox 14.38,41.08,14.42,41.11 ...        # min_lon,min_lat,max_lon,max_lat
dgd scan --aoi municipality.geojson ...            # any polygon file, only pixels inside it are reported
```

Leave out `--start` and `--end` and you get the last six months.

What happens:

1. It finds every Sentinel-2 pass over the area in the target window and in the same months of the three previous years.
2. It downloads only the pixels it needs, masks clouds, and caches everything under `~/.cache/dgd`.
3. It scores every pixel against its own history, groups the odd ones into leads, ranks them and writes the report.

A 3 x 3 km area takes 5 to 10 minutes the first time, mostly downloading. Run it again with different thresholds and it takes under a minute.

Or skip the typing: `dgd ui` opens a world map in your browser where you drag a box and press a button. See [map interface](ui.md).

## 3. Read the report

Open `report.html` in the output folder.

- **P001, P002, ...** are persistent leads: something changed and stayed changed. Start here.
- **T001, T002, ...** are transient leads: bare soil appeared for two weeks or more, then went away or the window ended. Dig and cover looks like this. So do pipe repairs.
- Each lead has an image clip: before, after, the change score, and a timeline of the score across every clear pass. The timeline is the most useful part. A dump is flat, then a step, then it stays up.
- **Appeared between** gives the last clear look where the spot was normal and the first one where it was not. The truth is somewhere in that interval. Clouds decide how wide it is.
- Leads marked *farming?* sit on cropland or are shaped like a whole field. They are ranked lower but not removed.

Then read the [field guide](field-guide.md) before anybody gets in a car.

## 4. Take it into the field

- `candidates.kml` opens in Google Earth and in most phone mapping apps.
- `candidates.csv` has a Google Maps link per lead.
- `candidates.geojson` and the `.tif` rasters go into QGIS or ArcGIS.

## 5. Tune

| Too many leads | Too few leads |
| --- | --- |
| `--min-score 4` | `--min-score 2.5` |
| `--min-area-m2 800` | `--min-area-m2 200` |
| `--exclude-landcover water,snow,cropland` | `--min-persistence 0.4` |
| `--no-transient` | `--baseline-years 4` |

See the [CLI reference](cli.md) for everything else.

## 6. Searching for a missing person

Two steps, two scales.

**Wide area, Sentinel-2.** Start at the date the person went missing. This finds machine-dug pits, burn spots and fresh tracks, not a hand-dug grave:

```bash
dgd scan --preset burial --since 2026-05-02 --aoi search_region.geojson --name "Case 114, wide area"
```

**Close up, drone or aerial imagery.** Fly the places that matter (the wide scan's leads, the phone's last known positions, the routes) and search the result:

```bash
dgd graves --after drone_ortho.tif --aoi search_area.geojson --name "Case 114, woodland" --output-dir outputs/case_114
```

Load `candidates.gpx` onto a handheld GPS or phone and walk them. Read [grave search](graves.md) first, including the part that says **do not dig**.

## 7. Run the tests

```bash
pytest               # offline, synthetic data, under a minute
pytest -m network    # also checks the live imagery archive
```
