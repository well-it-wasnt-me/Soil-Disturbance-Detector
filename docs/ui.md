# Map interface

For people who would rather point at a map than type coordinates, and for anyone keeping track of more than one search.

```bash
dgd ui
```

That starts a small server on this computer and opens it in your browser. The work still runs here, exactly as the command line would run it: same code, same downloads, same reports. Every job shows the exact `dgd` command it ran.

The left bar has five places: **Cases**, **New search**, **Jobs**, **Imagery** and **Settings**.

## Cases

A case holds everything about one investigation: its search areas, the searches run on them, every lead those searches found, what was decided about each lead, and a log. A case can be exported whole and handed over.

- **New case**: give it a name and a kind (missing person, illegal dumping, other). For a missing person, the date they went missing fills in the burial scan for you.
- **Overview**: the case's areas and leads on one map, the leads still waiting for review, the latest log entries.
- **Searches**: every search in the case, with its progress bar while it runs.
- **Leads**: every lead from every search, filtered by decision.
- **Log**: everything that happened, newest first. Searches queued and finished, decisions, status changes and notes. Add notes here.
- **Files and hashes**: every search writes `manifest.json` with its inputs, settings, software version and a SHA-256 of each output. *Check* recomputes those hashes and names any file that changed or went missing since. *Export case file* downloads one zip with the case record and every search folder in it.

Searches can also run without a case. They show up in Jobs, and a results page offers to add them to a case later.

## New search

1. **Find the place.** Type a town, an address or `lat, lon`, or just zoom. The layer button top right switches between satellite and street maps, and *Imagery from* shows Esri's older satellite and aerial imagery for any release date. That is for looking only and never used as input.
2. **Area.** Click *Draw box* and drag. The panel shows the size and a bar against the limit for the chosen search, which turns red when the box is too big.
3. **Look for.**
    - **Dumping and burning**: the Sentinel-2 scan. Pick the date window and how many previous years count as normal: up to as far back as Sentinel-2 imagery goes from that date (November 2016 at best, at most 10 years). Years before 2018 are flagged, because coverage is thinner there and missing in parts of the world. Up to 400 km².
    - **Burial, wide area**: the Sentinel-2 burial preset from the date the person went missing, with the same choice of how many previous years count as normal. It finds machine-sized digging, burning and tracks, not a hand-dug grave.
    - **Grave search**: high resolution. The imagery list fills in with the free sources covering your box, and the year lists show what each service has there. Or give the path of your own drone or aerial GeoTIFF. *See every year as a table* opens the Imagery page for the same box. The map shows where free high resolution imagery exists.
4. **Run.** The job tray over the map shows every job with a progress bar: which step it is on, and how far through the downloads.

## Results

*Review leads* on a finished search opens its results: the leads on the left, the map in the middle, the selected lead on the right.

- **Map layers**: the before and after imagery the search compared, and the score as a heat layer. *Swipe* puts before on the left and after on the right of a line you drag.
- **The lead**: its numbers, the image clip from the report, the score on every clear pass (Sentinel-2 searches), and why it scored: plants lost, bare soil, brightening, burn scar, or for grave searches the evidence layers.
- **Pass timeline** (Sentinel-2 searches): every pass as a tick, the selected lead's score as dots, and hazy passes boxed in red. Those were left out of dig detection.
- **Decisions**: *Checked*, *Field visit*, *Needs imagery* or *Dismiss*, with a reason and notes. They are saved in `review.json` in the search's folder and go into the case log.
- **Keys**: J and K move through the list, C marks checked, D dismisses, S toggles the swipe.

## Lead and field sheet

*Field sheet* on a lead opens it on its own page next to a printable A4 sheet: case and lead, a do-not-dig or do-not-touch warning, a small map, the coordinates in large print, a checklist for the people going out, and who to call (set in Settings). *Print* prints only the sheet. *GPX* downloads the point for a handheld GPS or phone.

## Jobs

Every job, running, waiting and finished, with the exact command it runs, its steps, and its live log. Jobs run one at a time, each in its own process.

- **Stop** ends a running job within a second or two. Imagery already downloaded stays in the cache, so running it again later is quick. **Cancel** takes a waiting job out of the queue.
- **Remove from list** and **Clear finished** hide jobs. Neither deletes anything: results stay under `outputs/ui/<job>/`.

## Imagery

Draw a box, and the page asks every free high resolution service which years it has there, as a table: one row per source, one column per year, pixel size in each cell, *IR* where near infrared exists. Click a year to search it, then an earlier year of the same source to compare against. The plan panel says what that pair gives you and whether the box fits the source's limit, and runs the grave search.

*Count Sentinel-2 passes* shows how many passes per month had little cloud over the box in the last four years. That is for timing and for the wide-area scans. Ten metre pixels never find a grave.

## Settings

Saved in `settings.json` in the output folder.

- **Storage**: how much the download cache holds, by kind, and *Clear* for each. Clearing only ever deletes folders the scans made, never anything else that happens to live in the cache folder, and it waits while a job is running.
- **Network and privacy**: switch off place search, the older imagery viewer, or all map tiles. Set your own Overpass server.
- **Imagery sources**: leave sources out of the grave search lists.
- **Search defaults**: change score, smallest lead and duration for dumping scans. Only values that differ from the shipped ones go on the command line.
- **Field sheets**: the contact line printed on every sheet.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `--port` | `8765` | Local port. If it is busy the next free one is used, and the terminal says which. |
| `--output-dir` | `outputs/ui` | Where job folders, cases and settings go. Restart with the same folder and everything comes back. |
| `--cache-dir` | `~/.cache/dgd` | Download cache, shared with the command line. |
| `--no-browser` | off | Start the server without opening a browser window. |

## Safety

The server listens on this computer only (127.0.0.1), and every request needs a token that only the page it served knows, plus a same-site cookie. Without that, any web page you happen to visit could tell your computer to start downloading imagery or read your results. Result files and case exports are served only from the job folders, and only to the page.

The page loads no web fonts and no analytics. The map tiles come from OpenStreetMap and Esri and the place search from OpenStreetMap's Nominatim, so the browser does talk to those services while you look around, unless you switch them off in Settings. The searches themselves fetch satellite imagery, land cover and map data, the same as the command line.
