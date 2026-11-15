# How it works

Written so it can be explained to a prosecutor, a judge or a skeptical sergeant. No step depends on a black box.

## The data

**Sentinel-2 Level-2A** surface reflectance from the European Space Agency, through Element84 Earth Search. 10 m pixels, a new look every 2 to 5 days depending on latitude, archive from 2017. Free, public, no login. ESA's reprocessed Collection 1 is used whenever it has the scene, and the older archive fills the gaps. Both were checked pixel by pixel against each other, so their brightness scales agree.

Six bands are used: blue, green, red, near infrared and two shortwave infrared bands. The shortwave bands matter most. They separate bare soil, rubble, plastic and ash from vegetation better than anything visible to the eye.

**Clouds** are removed per pixel with ESA's scene classification layer. Clouds, cloud shadow, cirrus, snow and saturated pixels are dropped. Dark pixels are kept on purpose, because burned waste is dark.

**ESA WorldCover 2021** supplies land cover, used to drop water and to tell the analyst what each lead sits on.

## Step 1: what is normal here, this time of year

For each pixel, four indices are computed for every clear pass:

| Index | Moves when |
| --- | --- |
| NDVI (vegetation) | drops when vegetation is removed or buried |
| BSI (bare soil) | rises when soil, rubble or fill is exposed |
| NBR (burn ratio) | drops after fire |
| Brightness | rises for plastic, concrete, white waste, and falls for ash |

The **baseline** is every clear pass in the same calendar months of the previous three years. From it the scanner takes the median (what normal looks like) and the median absolute deviation (how much normal wobbles). Both are robust: a few odd days in the baseline do not move them.

Using the same months matters. Plowing, harvest, mowing and leaf fall happen on a calendar. A field plowed every April has a big April wobble, so plowing it again in April scores low.

## Step 2: how strange is it now

For every pass in the target window, each index is turned into a **robust z-score**: how many "normal wobbles" away from the baseline median it sits, signed so that positive always means "more like dumping". A floor on the wobble stops perfectly calm pixels from turning sensor noise into a fake 40-sigma event.

**Regional correction.** A drought, a wet spring or a hazy day moves every pixel of the same land cover in the same direction. For every pass, the scanner measures the typical z-score of each land cover class across the whole area and subtracts it. A dry summer moves everybody. A dump moves one patch. What is left after the subtraction is the local part, which is the part that matters. On the Kidlington test (spring 2025, one of the driest in England in decades) it halved the persistent leads, 66 to 35. Without it the dump itself merged with drought-browned grass next to it and was not reported as a lead of its own.

The z-scores are combined into two signatures per pass (Stouffer's method, a plain average of z-scores scaled by the square root of their number):

- **material** = vegetation loss + bare soil gain + brightening. Fresh rubble, plastic, construction waste, scraped ground.
- **burn** = vegetation loss + burn ratio drop + darkening. Waste fires.

## Step 3: persistent leads

A dump arrives and stays. For each pixel the scanner tries every possible start date in the target window and takes the median score from that date to the end. The best one is the pixel's score. The step has to hold to the end of the window, for at least three clear passes spread over at least 14 days.

A plain median over the whole window would punish a dump for arriving late. If it showed up two months in, the two clean months drag the median down and a strong signal reads as a weak one. This is the fairer question.

**Persistence** is the share of clear passes after the step that are above the threshold. Defaults: score 3 or more, persistence 0.5 or more.

## Step 4: transient leads (dig and cover)

Burying waste means digging, which exposes soil. Then the hole is covered and, given time, grass grows back. The surface is bare for weeks, not days.

The **dig score** uses only vegetation loss and bare soil gain. Brightness is left out on purpose: haze brightens everything, but it lifts blue more than shortwave infrared, which pushes the bare soil index down. A hazy day scores low here while fresh soil scores high.

Two rules keep this honest:

1. **The pixel has to be actually bare.** Passes where NDVI is 0.3 or higher do not count, whatever the z-score says. Mowed hay, drought-brown pasture and stubble sit around 0.4 to 0.6. Fresh soil sits well under 0.3. This was checked on real passes. On the Kidlington test it took transient leads from 19 to 0, all of them hay and stubble.
2. **It has to last 14 days.** The event strength is the best minimum score over any run of clear passes spanning at least 14 days. Haze lasts a day. A backfilled pit lasts weeks.

Passes where more than 5% of the whole area lights up at once are treated as bad passes (missed cloud, smoke, haze) and sit out transient detection.

## Step 5: from pixels to leads

Flagged pixels are grouped into patches. Fragments within about 20 m of each other are merged, so one plowed field is one lead and not seven. Patches under 300 m2 are dropped.

Each lead gets:

- area, mean and peak score, persistence, land cover
- a **signature hint**: *brightening*, *soil exposure* or *burn*. These describe what the measurements did, not what the object is. At 10 m a waste heap and a plowed field can swap labels, and on the Kidlington test they did.
- a **farming flag** when it sits on cropland or is shaped like a whole field (2 ha or more and close to a rectangle). Flagged leads get half weight in the ranking. They are not removed.
- a **score timeline** across every clear pass, baseline included
- an **appearance window**: the last clear pass where it looked normal and the first where it did not

**Ranking** within each kind: mean score x persistence x log2(1 + pixels) x farming weight. Area enters through a logarithm so a 40 hectare construction site does not bury a strong 30 pixel dump by sheer acreage.

## What it cannot do

- **See small things.** 10 m pixels. A pile 15 m wide is one or two pixels across and mixed with whatever is next to it. Under about 300 m2, forget it.
- **See old things.** It finds change inside the target window relative to the baseline years. A dump that predates the baseline is invisible to it.
- **Tell a pile of waste from a pile of topsoil.** At 10 m with mixed pixels, the spectra of a waste heap and a freshly plowed field overlap. Context (shape, land cover, location) and the image clip are what separate them, and a human does that part.
- **See through clouds.** Persistently cloudy places and seasons get fewer clear looks and wider appearance windows. Pixels with fewer than 6 clear baseline looks or 3 clear target looks are not scored at all.

## Provenance

Every scan writes `manifest.json`: software version and git commit, the exact command, UTC timestamps, the area and grid, every Sentinel-2 granule used with its id and cloud cover, passes that failed or were set aside, every parameter, and a SHA-256 hash of every output file. Anyone with the manifest can rerun the scan and get the same leads.
