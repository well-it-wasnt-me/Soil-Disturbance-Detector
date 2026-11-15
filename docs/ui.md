# Map interface

For people who would rather point at a map than type coordinates.

```bash
dgd ui
```

That starts a small server on this computer and opens it in your browser. The page is a world map. The work still runs here, exactly as the command line would run it: same code, same downloads, same reports. Nothing else leaves the machine.

## Using it

1. **Find the place.** Type a town, an address or `lat, lon` in the search box, or just zoom. The layer button top right switches between satellite and street maps. *Look at older imagery* shows Esri's historical satellite and aerial imagery for any release date, which is the quickest way to see what a spot looked like before. It is for looking only and is never used as input to a scan.
2. **Select the area.** Click *Select area on the map* and drag a box. The panel shows its size. Results stay inside the box.
3. **Pick the job.**
    - **Trash**: the Sentinel-2 dumping scan. Choose the date window and how many previous years count as normal. Up to 400 km2.
    - **Burial, wide area**: the Sentinel-2 burial preset from the date the person went missing. Anywhere, up to 400 km2. It finds machine-sized digging, burning and tracks, not a hand-dug grave.
    - **Burial, grave search**: high resolution. The imagery list fills in with the free sources that cover your box (USA, Netherlands, France, Spain, parts of Germany, New Zealand, and OpenAerialMap patches anywhere), and the year lists show what each service actually has there, with pixel size and near infrared marked. Or give the path of your own drone or aerial GeoTIFF on this computer. Keep the box to a few hundred metres.

        The first time you open the Burial tab the map shows where free high resolution imagery exists (turn it off with the checkbox, or *Free high-res imagery* in the layer menu). Green: a national program flies the whole country or state, with near infrared. Purple: same, color only. Yellow dashed boxes: single OpenAerialMap images, shown once you zoom in to town level, because that is all they are. Hover any of them for pixel size, years and licence. The national edges are Natural Earth borders, good to about a kilometre either way. Older years were flown in pieces, which is why the year list still asks the service about your exact box.

4. **Watch the job list.** Jobs run one at a time, each in its own process. When one finishes, *Open report* shows the full report and *Show on map* draws the leads on the map, with a popup per lead linking to its image clip.

## Managing jobs

- **Cancel** takes a waiting job out of the queue.
- **Stop** ends a running job within a second or two. Imagery already downloaded stays in the cache, so running it again later is quick.
- **Remove** hides a finished job from the list. **Clear finished** does that for every finished, failed, stopped or cancelled job at once.

Neither Remove nor Clear deletes anything. Results stay under `outputs/ui/<job>/`, one folder per job, with the same files and manifest as the command line produces. Delete those folders yourself when the case allows it.

## Options

| Argument | Default | Meaning |
| --- | --- | --- |
| `--port` | `8765` | Local port. If it is busy the next free one is used, and the terminal says which. |
| `--output-dir` | `outputs/ui` | Where job folders go. Restart with the same folder and the job list comes back. |
| `--cache-dir` | `~/.cache/dgd` | Download cache, shared with the command line. |
| `--no-browser` | off | Start the server without opening a browser window. |

## Safety

The server listens on this computer only (127.0.0.1), and every request needs a token that only the page it served knows, plus a same-site cookie. Without that, any web page you happen to visit could tell your computer to start downloading imagery or read your results. Result files are served only from the job folders.

The map tiles come from OpenStreetMap and Esri and the place search from OpenStreetMap's Nominatim, so the browser does talk to those services while you look around. The scans themselves only fetch satellite imagery, land cover and map data, the same as the command line.
