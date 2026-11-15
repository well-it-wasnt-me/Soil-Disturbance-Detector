# Grave search

`dgd graves` searches high resolution imagery (drone, aerial survey or commercial satellite, 3 cm to 1 m per pixel) for grave-sized patches of disturbed ground, and ranks them for a search team to check.

It is a search aid for a lawful investigation with a defined area and a time window. It narrows where people walk, probe, bring dogs or ground radar. It does not replace any of them, and a clean result does not clear an area.

!!! danger "Do not dig"
    If a candidate looks like a grave on the ground, treat it as a possible crime scene. Stop, mark it, keep people and dogs off it, photograph it, record the position, and call the forensic team. Evidence destroyed by an enthusiastic shovel does not come back.

## What it looks for

A body is quiet from above. The digging is not. The forensic literature agrees on what a fresh clandestine grave changes at the surface (Koopman, Milliet and Champod 2025, *Forensic Science International* 367:112375):

- **Soil color.** Subsoil brought up and mixed in, lighter or darker than the topsoil around it.
- **Vegetation.** Gone at first, then patchy, sometimes later greener or a different species than its neighbors.
- **Shape.** About 1.5 to 2.5 m long and 0.5 to 1.2 m wide for one person. Long and narrow, not round.
- **Height.** A low mound of backfill at first, often only centimeters. It settles over weeks to months and can turn into a dip once the remains collapse. One drone study measured mounds of 3 to 17 cm settling to 1 to 3 cm over 18 months, and still picked them out of the surface model (Alawadhi, Eliopoulos and Bezombes 2024, *Drones* 8:444).
- **Temperature.** Backfill can run a couple of degrees off its surroundings for the first weeks.
- **Concealment.** Branches, leaves, debris dumped on top. Which, ironically, is its own anomaly.

Most studies cannot tell a grave from an empty pit dug for comparison. So a hit means "ground was disturbed here". Only excavation by the right people says what is underneath.

## How it works

Three detectors, used when their inputs exist, fused into one evidence map:

| Layer | Needs | Looks for |
| --- | --- | --- |
| `anomaly` | one image | A grave-sized patch that differs from the ring of ground around it, measured against how much that ring itself varies (local Reed-Xiaoli detector, run at two pixel sizes). Works without a "before" image, which in a real case usually does not exist. |
| `change` | before and after | The same, on the color change between the two dates. Whole-scene differences (season, sun, camera) cancel out, local oddities stay. |
| `relief` | surface model | Mounds and dips of a few centimeters relative to the local ground. |
| `thermal` | thermal image | Warm or cold grave-sized patches. Best days to a few weeks after burial, at dawn or dusk. |

Then:

1. **Dig direction.** Patches that went from plants to soil are kept, patches that went the other way (new lawn, a tarp that blew off) are pushed down.
2. **Shape.** Patches are measured. Long and narrow, grave-sized patches move up. Round ones (dung patches, molehills, bushes) move down. Tolerances widen at coarse resolution, where shape is a guess.
3. **Places nobody digs.** Mapped buildings, paved roads, car parks, paved plazas and water from OpenStreetMap are masked. Roofs and parked cars are a change detector's favorite false alarm. Your own polygons (a land registry, areas already searched on foot) go in with `--mask-out`.
4. **Access.** Distance to the nearest mapped road, track or path. Studies of solved cases put most burial sites within about 100 m of one, often much closer to where the vehicle stopped. Near ranks higher, far still counts.

Reported sizes describe the detection footprint, which is typically 0.3 to 0.6 m larger than the disturbed patch itself.

## Running it

Your own imagery:

```bash
dgd graves --after drone_ortho.tif --name "Case 2026-114, woodland north of farm" \
           --aoi search_area.geojson --output-dir outputs/case_114
```

With an earlier image for change detection, a surface model, and a multispectral camera without a blue band:

```bash
dgd graves --after flight_june.tif --before flight_march.tif --dsm flight_june_dsm.tif \
           --bands sequoia --before-bands sequoia --aoi search_area.geojson
```

Free imagery where it exists. First ask what is there, then search:

```bash
dgd imagery --lat 47.877 --lon 1.945
dgd graves --fetch ign-fr --lat 47.877 --lon 1.945 --radius-m 150 --after-year 2023 --before-year 2020
dgd graves --fetch pdok --lat 52.11 --lon 4.88 --radius-m 150 --after-year latest --before-year 2025
```

## Free high resolution imagery

Every source below is free, needs no account, is fetched automatically, and is licensed for this use. National programs fly each region every one to three years, so which years exist depends on the exact place. `dgd imagery` and the map interface ask the services rather than guess. The [map interface](ui.md) also draws where each source covers the ground.

| `--fetch` | Where | Pixel | Years | Near infrared | Licence |
| --- | --- | --- | --- | --- | --- |
| `naip` | USA, lower 48 | 60 cm (1 m before 2016) | every 2 to 3 years | yes | public domain |
| `pdok` | Netherlands | 8 cm from 2021, 25 cm before | every year | most years | CC BY 4.0 |
| `ign-fr` | France, mainland | 20 cm | 2001 on, per departement | 2012 on | Licence Ouverte 2.0 |
| `pnoa-es` | Spain, mainland | 25 cm (50 cm before 2014) | 2004 on, per region | no | CC BY 4.0 |
| `nrw-de` | Germany, North Rhine-Westphalia | 10 cm latest, 20 cm history | 2000 on | latest flight | dl-de/zero-2.0 |
| `bayern-de` | Germany, Bavaria | 40 cm | latest only | no | CC BY 4.0 |
| `linz-nz` | New Zealand | 5 to 75 cm | often decades of flights | no | CC BY 4.0 |
| `oam` | anywhere, in patches | 1 to 30 cm, drones and satellites | whatever was uploaded | rarely | per image, usually CC BY 4.0 |

`latest` is accepted wherever a year is, and means the most recent national mosaic.

### Elsewhere

These are free or cheap but cannot be fetched automatically, so get the image yourself and pass it with `--after` (and `--before`):

- **United Kingdom.** The Environment Agency's vertical aerial photography, 10 to 50 cm, RGB and NIR, comes as 5 km zipped ECW tiles from the Defra Data Services Platform. Convert ECW to GeoTIFF in QGIS. The licence statements on its records disagree with each other, so read the one on the dataset you download.
- **Other German states.** Most run their own geoportal. Several publish orthophotos as open data, some only for viewing.
- **Australia.** ELVIS and the state catalogues, a mix of free and paid.
- **The European Data Portal** catalogues many national datasets. Useful for finding them, not for downloading pixels.
- **Commercial satellites** (Airbus OneAtlas, SkyFi, EOSDA LandViewer and others) sell 30 to 50 cm archive scenes, some with free trials. A scene your agency buys goes in like any drone image.
- **Google Earth Pro** shows historical imagery almost everywhere and is the best free way to look at a candidate by eye. It offers no download for analysis.
- **Esri World Imagery Wayback** offers years of historical imagery for viewing. The map interface shows it as a layer. Its terms cover looking at it, not running detectors on it, so it is never used as an input.

OpenStreetMap comes from the public Overpass servers by default, which are run by volunteers and are sometimes down. For reliable or offline work, download a free extract of your country or province from [Geofabrik](https://download.geofabrik.de/) once and pass it with `--osm-file region.osm.pbf`. Agencies running their own Overpass server can point at it with `--overpass-url` or the `DGD_OVERPASS_URL` environment variable. Without any of them the search still runs, without the masks and the access ranking, and the report says so.

Band orders: `rgb`, `bgr`, `rgbn`, `bgrn`, `nrg` (color infrared), `sequoia` and `m3m` (green, red, red edge, NIR), `p4m` (blue, green, red, red edge, NIR), `micasense` (blue, green, red, NIR, red edge), or a comma list such as `blue,green,red,skip,nir`. Check the band order of your own orthomosaic export, every software package has opinions.

Outputs: `report.html` with close-up clips 12 m across, `candidates.gpx` for handheld GPS units and phones, KML, GeoJSON, CSV, the evidence raster, and a manifest with SHA-256 hashes of the input images and every output.

## Flying for it

From the studies and from what the detector needs:

- **Pixel size 10 cm or finer.** Most camera drones beat that at any legal survey height. Multispectral cameras have far fewer pixels, so check their ground sample distance at your planned altitude before you trust it. The detector works at 10 cm by default and averages finer imagery down, which also smooths out grass blades nobody needs to see.
- **Near infrared if you can get it.** In the drone study above, NIR was the best band on 11 of 16 survey dates and red edge on the other 5. Older graves (weeks to years) are where plain RGB gives up first. Multispectral drones with red edge (several common models) add the chlorophyll-sensitive index reported to catch graves at three to four months.
- **Calibrate** multispectral cameras with a reflectance panel before and after the flight.
- **Make a surface model** from the same flight (75 percent overlap or more). Better still, LiDAR, which sees the ground under vegetation where photogrammetry cannot.
- **Leaf-off season under trees.** Optical imagery does not see through canopy, full stop.
- **Thermal** helps for fresh graves only: days to a few weeks, flown at dawn or dusk when the ground's temperature differences show.
- **Fly again later.** A second flight weeks later is the best "before and after" there is, and settling ground changes between flights.
- **Older imagery** of the area (national aerial photo programs, earlier commercial scenes, an earlier flight by anyone) turns a single-image search into a change search. Ask around before you fly.

## How well it works

See [evaluation](evaluation.md#grave-search) for the full numbers. The short version, from synthetic graves painted into real 8 cm aerial photos and checked blind:

- **Fresh grave on grass or a young crop, 10 cm imagery:** seen 11 to 14 times out of 15, and more than half of them are reached within the first five false alarms.
- **Grass growing back over it:** still mostly seen, harder to rank. Near infrared helps.
- **Sod carefully put back:** one or two out of 15 near the top. Careful concealment beats cameras.
- **Plowed field:** seen somewhere in the list, almost never near the top.
- **Coarser imagery:** at 60 cm about half of the 10 cm performance is left. Over a big area at 60 cm the clutter wins, as a real cemetery test showed.

Fly as fine as you can, with near infrared if you can, and keep the search area tight. In the tests, a fresh grave on grass usually sat within the first 5 to 20 candidates of a 4 hectare search. Older and better hidden graves sat much deeper or not at all. A ranked list of spots is still a lot less walking than every square metre, which is the point.

## What it cannot do

- Tell a grave from any other hole. Disturbed soil is disturbed soil.
- See under trees, under snow, under standing water, or under a carefully replaced sod.
- Make bad imagery good. Blurry, badly stitched or wrongly georeferenced mosaics produce blurry, badly stitched candidates.
- Work from Sentinel-2. A single grave is 2 percent of one 10 m pixel. For the bigger picture (machine-dug pits, mass graves, fresh tracks to a remote spot) use `dgd scan --preset burial --since <date>` on the wider area first, then fly the drone over what it finds.

## Responsible use

This is built for investigators with a lawful search mandate, a defined area and a time window. Results name places, and places have owners and neighbors. Treat candidates as unverified intelligence: need to know only. A candidate that turns out to be a dead badger should not become a story in the local paper.
