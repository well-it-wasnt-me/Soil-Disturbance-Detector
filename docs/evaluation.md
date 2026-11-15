# Evaluation

How well does it work? Here is what was measured, how, and what it does not tell you. Small numbers, honestly reported, beat big numbers nobody can reproduce.

## The test set

Seven places where new waste is known to have appeared at a known time. None of them were used to design the method beyond the Kidlington case described below.

**Kidlington, Oxfordshire, UK.** In 2025 roughly 20,000 tonnes of mixed waste were dumped on a field near Hampton Poyle, between the A34 and the River Cherwell. The Environment Agency rated it high-risk on 2 July 2025 and obtained a closure order on 23 October 2025. The pile was about 150 m long. The exact coordinates were never published, so the test location was narrowed from the official description using OpenStreetMap (where the river and the A34 run close together) and then confirmed visually in the scan's own before and after images. Kidlington was also the case used while developing the method, so treat it as a worked example, not an independent test.

**Six Southeast Asian sites from Global Plastic Watch** (Indonesia x3, Myanmar, Thailand, Vietnam). Global Plastic Watch publishes quarterly footprints of human-verified waste sites (Kruse et al. 2023, PLOS ONE 18(1): e0278997, data CC BY-NC 4.0). A case was taken where a footprint stayed small for two quarters and then grew by at least 1 hectare and stayed grown for two quarters, between late 2019 and early 2021. The new ground (later footprint minus earlier footprint) is the answer key. These were not looked at before running the scanner on them.

## Settings

Defaults throughout: score 3, persistence 0.5, minimum area 300 m2, at most 5 passes per fortnight. Kidlington: 3 x 3 km area, target April to September 2025, 3 baseline years. Global Plastic Watch: 2 x 2 km around each site, target window from a month into the "before" quarter to five months after the start of the "after" quarter, 2 baseline years (the archive gets thin before 2018).

A case counts as a hit if a persistent lead touches the answer, with 30 m of slack for co-registration.

## Results

| Case | Leads in area | Rank of the true site | Notes |
| --- | --- | --- | --- |
| Kidlington, UK | 41 | P002 | The dump. P001 is a large field converted from grass to bare soil in August. |
| Indonesia 211 | 22 | **P001** | Burn signature: the new waste was burning. |
| Myanmar 1298 | 41 | P002 | |
| Indonesia 74 | 34 | P004 | |
| Vietnam 550 | 67 | P005 | |
| Indonesia 190 | 70 | P019 | Dense peri-urban change all around. |
| Thailand 5025 | 43 | P034 | Found, but buried in the list. |

- **Found at all: 7 of 7.** Every site showed up as a persistent lead.
- **In the top 5: 5 of 7.**
- **Number one: 1 of 7.**
- **Leads per square kilometre: median about 10**, from 4 in rural England to 17 in fast-growing Javanese suburbs.

### Stricter settings

| Setting | Sites found | In top 5 | Leads per km2, median (range) |
| --- | --- | --- | --- |
| score 3, area 300 m2 (default) | 7 of 7 | 5 | 9.9 (4.4 to 16.8) |
| score 3.5, area 300 m2 | 6 of 7 | 5 | 8.7 (3.3 to 15.9) |
| score 4, area 300 m2 | 5 of 7 | 5 | 7.2 (1.9 to 10.1) |
| score 3, area 600 m2 | 6 of 7 | 5 | 7.0 (2.7 to 12.3) |
| score 3.5, area 600 m2 | 6 of 7 | 5 | 6.1 (2.3 to 10.3) |

Stricter settings trim the tail of the list and nothing else. Every variant keeps the same five sites in its top five. The sites they lose are the ones ranked 19th to 34th by the default, which nobody would have reached anyway. So working the top of the default list is roughly the same as running stricter settings, except nothing gets thrown away silently. The defaults stay where they are. If the list is too long, read less of it, or tighten the settings and know what you are trading.

## What this means in practice

**It sees new dumping reliably.** On this small set, every known site produced a lead, across a temperate English summer and four tropical countries with their clouds.

**It does not rank well enough to trust the top of the list blindly.** In peri-urban areas a lot of ground changes for perfectly legal reasons: construction, land clearing, quarrying, farming. The scanner cannot tell those from dumping at 10 m, and it says so. Budget a few seconds per lead to look at the image clip, and work a list of 20 to 40 per few square kilometres. That is what it currently takes.

**Ten leads per square kilometre does not scale to a whole province** without help. For large areas, use the rankings, exclude cropland if that suits the local pattern, raise the thresholds, or scan the places people actually dump first: near roads, in quarries, along rivers, at the edge of town.

**The biggest improvement available is feedback.** Analysts marking leads as dump, construction, farming or nothing would teach the ranking what matters locally. See the [roadmap](roadmap.md).

## What this does not tell you

- Seven cases is a small sample. The numbers above are an estimate with wide error bars, not a guarantee.
- The Global Plastic Watch footprints come from their own model. Some "growth" may be their model reacting to clouds. The selection rules filter most of that, not all.
- Precision is not measured. Nobody went to check the other 30 leads in each area. Some of them are probably dumps nobody has reported.
- Dig and cover burial was not tested on real cases, only on synthetic data, because no dated, located public cases were found. If you have some, they are the most valuable thing you could contribute.

## Reproduce it

```bash
python scripts/evaluate_gpw.py --cases 12 --parallel 2
dgd scan --lat 51.824 --lon -1.266 --radius-m 1500 --start 2025-04-01 --end 2025-09-30 --output-dir eval/kidlington
```

The script downloads the Global Plastic Watch contours into your cache at run time and writes everything under `eval/`, which git ignores. Their data is non-commercial, this repository is public domain, and the two do not get mixed.

## Grave search

There is no public archive of imagery of clandestine graves with their locations, and there should not be. So `dgd graves` was tested three ways, none of them perfect, each honest about what it shows.

### 1. Synthetic graves in real landscapes (blind)

Graves were painted into real 8 cm Dutch aerial photos of a cattle pasture with ditches, a crop field against a woodland edge, and a plowed field: 15 per landscape, rotated rectangles 1.6 to 2.4 m by 0.6 to 1.0 m with ragged edges, in three states. **Fresh**: bare soil, mild clod texture, scattered spoil. **Regrowing**: grass back over about half of it, in clumps. **Turf**: sod put back over it by somebody careful, leaving slightly stressed grass and cut lines a few centimeters wide. The detector was not told where they were. Results at 25 and 60 cm come from averaging the 8 cm image down, as a coarser sensor would.

The number that matters for a search team is how many wrong spots they visit before reaching the grave. The table counts graves found with **at most 5 false alarms ranked above them**, out of 15.

| Ground | Grave | Imagery | 10 cm | 25 cm | 60 cm | Found at all (10 cm) |
| --- | --- | --- | --- | --- | --- | --- |
| Pasture | fresh | RGB + NIR | 8 | 5 | 4 | 14 |
| Pasture | fresh | RGB only | 7 | 5 | 4 | 13 |
| Pasture | fresh | before/after, RGB + NIR | 8 | 6 | 4 | 13 |
| Pasture | regrowing | RGB + NIR | 7 | 6 | 3 | 13 |
| Pasture | regrowing | RGB only | 5 | 4 | 3 | 12 |
| Pasture | regrowing | before/after, RGB + NIR | 6 | 6 | 2 | 13 |
| Pasture | sod put back | RGB + NIR | 2 | 2 | 1 | 11 |
| Pasture | sod put back | RGB only | 1 | 0 | 1 | 9 |
| Crop field edge | fresh | RGB + NIR | 9 | 8 | 6 | 11 |
| Crop field edge | fresh | before/after, RGB + NIR | 10 | 9 | 5 | 14 |
| Crop field edge | regrowing | RGB + NIR | 8 | 8 | 3 | 10 |
| Plowed field | fresh | RGB + NIR | 1 | 3 | 2 | 13 |
| Plowed field | regrowing | RGB + NIR | 1 | 0 | 0 | 6 |

All rows are out of 15 graves. "Found at all" counts graves with any candidate on them, however far down the list. False alarms at the default threshold run from about 60 per hectare (crop field) to 140 (plowed field, pasture with cattle). Most of them rank below the graves, which is the point of the ranking, but on pasture and plowed ground the lookalikes are real competition.

What it says:

- **Fresh graves on grass or young crops:** usually seen (11 to 14 of 15 at 10 cm) and in the 10 cm imagery more than half are reached within five false alarms. Resolution matters: at 60 cm roughly half of that is left.
- **Grass growing back:** still mostly seen, harder to rank. Near infrared earns its keep here (7 against 5 on pasture at 10 cm).
- **Sod carefully put back:** seen in the evidence map more often than you would expect, but drowned by lookalikes. One or two of 15 within five false alarms. To any optical method, careful concealment works.
- **Plowed field:** mostly seen somewhere in the list, almost never near the top. Soil on soil, with clods and tractor tracks for camouflage.
- **Before/after a year apart** barely beats a single image, because a year of farming and weather changes plenty besides the grave. A "before" from days or weeks earlier should do much better. That could not be tested with free imagery, so it is a hope, not a number.
- Fifteen graves per cell is a small sample. Differences of one or two between cells are noise.

This measures what the published drone studies could not: their test plots were fenced, flat and empty, and they could not image anything outside them to count false alarms (Alawadhi et al. 2024 say so in their limitations). It does not prove the detector finds real graves. Real graves get covered with leaves and branches by people who do not want them found. Treat these numbers as an upper bound.

Reproduce: `python scripts/evaluate_graves_synthetic.py`

### 2. Real new graves in real cemeteries

Cemeteries are the one place where fresh graves are legal, public and photographed from the air every year, which makes them honest test material: real soil, real grass, real digging, real clutter.

**De Nieuwe Ooster, Amsterdam (PDOK, 8 cm, spring 2025 against spring 2026, 25 ha).** Candidates were checked by eye in the before and after images. Most real hits were new white grave slabs, because Dutch graves get covered quickly, and one or two looked like fresh soil under trees. The same imagery was run three times as the masking improved:

| Run | New or changed graves in top 30 | False alarms in top 30 |
| --- | --- | --- |
| No masks | about 18 | 8 (moved cars, rooftops and skylights, shadows on pavement) |
| OpenStreetMap buildings, roads, water | about 19 | 7 |
| Plus car parks and paved plazas, read offline from a Geofabrik extract | about 21 | 5 (cars at a car park edge, a roof, lamp-post shadows on a footpath) |

The masks exist because of this test. A cemetery is target-rich, so its precision flatters the detector compared with a search for one grave.

**Riverside National Cemetery, California (NAIP, 60 cm, April 2020 against May 2022, about 5 km2): a miss.** No recognizable fresh grave among the top 24 candidates. They were small objects and red flowers on the lawns, the edges of cemetery drives (wider than the road widths assumed for masking), dry or bare ground, construction, and a bunker on the golf course next door. Before any thresholding the area produced about 19,700 candidates. Two things work against this test: national cemeteries resod a grave within days, so a once-a-year summer flight rarely catches one open, and at 60 cm a grave is four pixels. The synthetic test says the same in numbers: at 60 cm about half of the 10 cm detections are left. For grave searches, 60 cm imagery over a large area is close to useless. Over a small, well defined area with fresh disturbance it is a maybe.

### 3. Sentinel-2 and a documented mass grave: a miss

**Manhush, Ukraine, spring 2022.** About 340 m of burial trenches were dug at the north-western edge of the village from late March 2022, documented in commercial satellite imagery and since mapped in OpenStreetMap. `dgd scan --preset burial --since 2022-03-15` over 25 km2 did **not** find it. No lead touched the mapped site. Inside it the persistent score had a median of 0.5 (threshold 3) and dig events peaked at 2.9 (threshold 3.5).

The time series shows why. When the trenches were dug, the strip was indistinguishable from the bare spring fields around it: soil next to soil. Only six to eight weeks later, when the fields greened up and the grave strip did not, did a difference appear, and at 0.04 to 0.08 NDVI on 10 m pixels it was far below anything a sane threshold would flag. Imagery at 30 to 50 cm saw the trench lines immediately.

The lesson is stated where people will read it: Sentinel-2 is a long shot even for mass graves dug into bare ground. For graves, plan on drone or high resolution imagery from the start, and use the burial preset only to look for the bigger, louder things around a disappearance.
