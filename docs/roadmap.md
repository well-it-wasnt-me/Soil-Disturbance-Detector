# Roadmap

What exists, what comes next, and what stays out of reach of free 10 m imagery no matter how clever the code gets.

## Done

- Unsupervised scan with seasonal baselines, regional drift correction, persistent and transient (dig and cover) leads
- Free imagery, no account, cached, parallel downloads
- Reports for humans (HTML, image clips), for GIS (GeoJSON, GeoTIFF), for the field (KML, map links) and for the record (manifest with hashes)
- Supervised classify mode with honest, polygon-held-out validation
- Evaluation against a known UK site and dated waste-site growth from Global Plastic Watch

## Next: waste

- **Old burials.** A filled pit often grows different vegetation from the rest of the field for years: stressed, patchy, or greener. Detect patches whose vegetation keeps diverging from their own field across seasons, long after any dig event.
- **Long-standing dumps.** Change detection cannot see what was already there. A classifier trained on confirmed waste sites (Global Plastic Watch has thousands) could flag existing sites. That needs care with licensing, their data is non-commercial.
- **Field-relative scoring.** Compare a patch with the rest of its own field, using field boundaries from open data where they exist. A plowed field changes everywhere. A burial changes one corner.
- **Better ranking from feedback.** Let analysts mark leads as dump, farming, construction or unknown, and learn the ranking from that. The single biggest improvement available, and it needs users.
- **Larger areas.** Region-sized scans split into tiles, with a merged report.
- **Monitoring mode.** Rescan a list of areas monthly and report only what is new since last time.

## Graves: done

- `dgd graves`: single-image anomaly, before/after change, surface model relief and thermal layers, grave-shaped filtering, OpenStreetMap building and road masks, access ranking, GPX waypoints
- Support for multispectral drone cameras with red edge, with or without a blue band
- Free imagery where governments publish it: USA (NAIP), Netherlands (PDOK), France (IGN), Spain (PNOA), Germany (NRW, Bavaria), New Zealand (LINZ), plus OpenAerialMap anywhere. More national services are a registry entry each, see `src/dgd/highres_sources.py`.
- `dgd scan --preset burial --since <date>` for the Sentinel-2 scale: machine-dug pits, mass graves, tracks
- Blind synthetic evaluation in real landscapes, measuring false alarms per hectare

## Graves: next

- **Real cases.** The single most valuable contribution anyone could make: imagery of known, dated clandestine graves (or of forensic research sites) to test against. Synthetic graves are an upper bound.
- **Visibility and diggability priors.** Studies of solved cases find graves out of sight (most had under 50 percent visibility) and in diggable ground, within an hour of a town. A viewshed from roads and houses plus slope and soil maps would sharpen the ranking.
- **LiDAR ground models** as a first-class input, with mound and settling-dip detection tuned on published settling curves.
- **Search team feedback.** Mark candidates as checked, cleared or of interest in the field and feed that back into the next flight's ranking.

These are for investigators with a lawful search mandate, a defined area and a time window. Nobody needs a tool that scans the planet for graves, and this one is not going to become it.

## Will not happen

- Seeing anything under tree canopy with optical imagery.
- Telling legal from illegal. That is a paperwork question, and the paperwork lives with the authorities, not in a satellite.
