# Field guide

For the people who decide whether a lead is worth a car, a drone or a warrant. Read it before the first scan, 
not after the first wasted trip.

## What a lead is

A lead is a patch of ground that changed during your target window in a way it did not change in the same season 
of previous years. That is all. 
The software does not know what waste is. It knows what "unusual for this spot, this time of year" looks like, 
and it is good at that.

Most leads will turn out to be boring. That is fine. The job of the scanner is to turn "somewhere in this municipality" 
into "these twenty places", and the job of a person is to turn twenty into two.

## Triage in five minutes per lead

Open `report.html` and work down the persistent list first.

**1. Look at the timeline under the image clip.** This beats every other clue.

| Timeline shape                                                          | Usually means                                                                              |
|-------------------------------------------------------------------------|--------------------------------------------------------------------------------------------|
| Flat, then a step up, stays up                                          | Something arrived and stayed. Dumps, fill, new hardstanding, construction. **Best leads.** |
| Steps up at the very end of the window                                  | Too new to judge. Rescan in a month.                                                       |
| Spikes every year at the same time (visible in the grey baseline years) | Farming or seasonal. Usually not interesting.                                              |
| Up for a few weeks, then back down                                      | Dig and cover, pipe or cable works, mowing, a crop cycle. These are the transient leads.   |
| Ragged, up and down                                                     | Clouds and haze leaking through. Low confidence.                                           |

**2. Look at the before and after images.** Same colour stretch on both, so a difference you see is a difference that exists.

- A whole field changing colour, edge to edge, along the field boundaries: plowing or harvest.
- A patch in a corner, along a hedge, at a field gate, next to a lay-by, inside a quarry or behind a farm building: that is where people dump.
- Something linear along a road or a track: works, or tipping from vehicles. Look closer.

**3. Check the signature hint and land cover.**

- *brightening*: the patch got brighter. Plastic, concrete, rubble, white goods, also new gravel and new roofs.
- *soil exposure*: the patch went from green to bare. Earthworks, fill, excavation, also plowing.
- *burn*: dark and burn-like. Waste fires, also stubble burning and wildfire.
- *farming?* on cropland or field-shaped. Lower odds, but not zero.

The hints describe the measurements, not the object. At 10 m a pile of waste next to a hedge and a plowed field can trade labels.

**4. Look at better imagery before anybody drives.** Click the Google Maps link in the CSV or open the KML in Google Earth. Commercial basemaps are sharper than Sentinel-2, sometimes much older or much newer, and their date is usually shown. Google Earth Pro has a historical imagery slider, which is gold for this. If your agency has access to commercial or aerial imagery, this is where it pays for itself.

**5. Decide.** Dismiss, monitor (rescan later), or send someone.

## What fools it

| Looks like a lead                        | Tell-tale sign                                                                                               |
|------------------------------------------|--------------------------------------------------------------------------------------------------------------|
| Plowing a field that was grass for years | Whole field, straight edges, soil exposure. Turns green in the following months.                             |
| Construction sites                       | Near buildings, roads being built, planning notices. Your local council knows.                               |
| Quarries and gravel pits expanding       | Inside an existing pit, on a slope. Legal or not is a paperwork question.                                    |
| Forestry felling                         | Tree cover land class, large, regular.                                                                       |
| Hay and silage cuts                      | Grassland in early summer. Mostly filtered, some get through as persistent if the cut is late in the window. |
| Floods and drying wetland                | Next to water. Often follows a wet spell.                                                                    |
| Solar farms, greenhouses, plastic mulch  | Bright, regular rows. Plastic mulch on fields is the nastiest lookalike for plastic waste.                   |
| Missed clouds and haze                   | Ragged timeline, often one or two passes only.                                                               |

## What it misses

- Anything smaller than about 300 m2 at the surface. A pickup truck of fly-tipping is invisible.
- Anything that was already there before the baseline years.
- Waste dumped under tree canopy.
- Waste dumped and covered with soil in a field that gets plowed anyway. The ground looked like plowing because it was.
- Long stretches of cloud. Check the number of target passes in the report header. Under ten and the scan is half blind.

## Time stamps

"Appeared between X and Y" means the last clear pass that looked normal was X and the first that did not was Y. Cloudy weather widens the gap. It is a bracket on when the ground surface changed, not when anybody broke the law. Do not read more into it than that.

## Evidence and records

The scanner produces leads for investigation. It does not produce evidence of an offence, and nothing in the report should be described as such.

What it does produce is a record of how the lead was found. `manifest.json` lists the software version, every Sentinel-2 granule used, every parameter, and a SHA-256 hash of every output file. Keep the whole output folder unmodified with the case file. Rerunning the same command against the same archive produces the same leads, which is what you want when somebody asks.

Sentinel-2 imagery is public and so is this software. Anyone can check the work. That is a feature.

## Sharing results

A list of suspected illegal dump sites is sensitive. It names land, and land has owners. Treat the output like any other unverified intelligence: need to know, not the press, not social media. A lead that turns out to be a farmer plowing their own field should not become a story about them.

## Burial of waste

Burying waste needs a hole, and a hole is bare soil for days to weeks. That is what transient leads look for: soil exposure for at least 14 days in a place that does not normally get dug. On farmland the plowing calendar hides a lot of this, which is why farmland leads are kept but ranked lower rather than thrown out.

Months to years later, a filled pit can still show as a patch of stressed or odd vegetation compared to the rest of the field. The scanner does not look for that yet. See the [roadmap](roadmap.md).

## Missing persons and graves

Sentinel-2 cannot see a hand-dug grave. `dgd scan --preset burial --since <date>` narrows a wide area to machine-sized digging, burning and fresh tracks after a disappearance. The grave itself needs drone or aerial imagery and `dgd graves`, which has its own guide: [grave search](graves.md). The rules there are stricter than here, because the stakes are. A dump lead that turns out wrong wastes an afternoon. A grave dug up by the wrong people with the wrong tools can lose a case.
