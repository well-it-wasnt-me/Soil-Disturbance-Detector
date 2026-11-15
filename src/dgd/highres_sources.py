"""Free high resolution imagery, wherever a government or a community publishes it.

Every source here is free, needs no account, can be fetched by a program,
and is licensed for this kind of use. Each one writes an ordinary GeoTIFF,
so the rest of the code cannot tell a national archive from a mosaic
somebody stitched in a van.

| key       | where                  | pixel     | years           | NIR             |
| --------- | ---------------------- | --------- | --------------- | --------------- |
| naip      | USA, lower 48          | 0.6 m     | every 2-3 years | yes             |
| pdok      | Netherlands            | 8-25 cm   | yearly          | most years      |
| ign-fr    | France (mainland)      | 20 cm     | 2001 on         | 2012 on         |
| pnoa-es   | Spain (mainland)       | 25-50 cm  | 2004 on         | no              |
| nrw-de    | Germany, NRW           | 10-20 cm  | 2000 on         | latest          |
| bayern-de | Germany, Bavaria       | 40 cm     | latest only     | no              |
| linz-nz   | New Zealand            | 5-50 cm   | many            | no              |
| oam       | anywhere, patchy       | 1-30 cm   | whatever exists | rarely          |

National programs fly each region every one to three years, so "which
years exist here" depends on where here is. ``available`` asks the services
instead of guessing.

Sources that are free but cannot be automated (no API, account needed,
proprietary formats or unclear licences) are listed in docs/graves.md.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from dgd.highres import grid_from_bounds

log = logging.getLogger(__name__)

USER_AGENT = "Soil-Disturbance-Detector (free tool for law enforcement)"
LATEST = "latest"


def _get(url: str, timeout: int = 60, headers: dict | None = None, retries: int = 3) -> bytes:
    last: Exception | None = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404, 416):
                raise
            last = exc
        except Exception as exc:  # resets, timeouts, the usual internet weather
            last = exc
        time.sleep(1 + 2 * i)
    raise last  # type: ignore[misc]


def _write_geotiff(path: Path, arr: np.ndarray, crs: str, transform, descriptions=None, nodata=None) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "GTiff", "height": arr.shape[1], "width": arr.shape[2], "count": arr.shape[0],
               "dtype": arr.dtype.name, "crs": crs, "transform": transform, "compress": "deflate", "tiled": True}
    if nodata is not None:
        profile["nodata"] = nodata
    if arr.shape[0] == 4:
        # GDAL calls band 4 of any 8-bit 4-band file alpha unless told otherwise
        # at creation. Told otherwise here, so QGIS shows NIR as NIR.
        profile["alpha"] = "UNSPECIFIED"
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        for i, d in enumerate(descriptions or [], start=1):
            dst.set_band_description(i, d)
        if arr.shape[0] == 4:
            from rasterio.enums import ColorInterp

            dst.colorinterp = [ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.undefined]
    return path


def parse_year(text) -> int | str:
    """'2024' -> 2024, 'latest' -> 'latest'."""
    if text is None:
        raise ValueError("year missing")
    t = str(text).strip().lower()
    if t in (LATEST, "current", "now"):
        return LATEST
    if re.fullmatch(r"\d{4}", t):
        return int(t)
    raise ValueError(f"year must be YYYY or 'latest', got '{text}'")


@dataclass
class Available:
    """One year (or 'latest') a source has imagery for at a place."""

    year: int | str
    nir: bool
    res_m: float
    label: str = ""

    def as_dict(self) -> dict:
        return {"year": self.year, "nir": self.nir, "res_m": self.res_m, "label": self.label or str(self.year)}


class Source:
    key = ""
    name = ""
    coverage = (-180.0, -90.0, 180.0, 90.0)
    res_m = 1.0
    pixel_note = ""
    years_note = ""
    nir_note = ""
    license = ""
    citation = ""
    max_km2 = 2.0

    def covers(self, bbox) -> bool:
        c = self.coverage
        return bbox[0] >= c[0] and bbox[1] >= c[1] and bbox[2] <= c[2] and bbox[3] <= c[3]

    def available(self, bbox, cache_dir: Path | None = None) -> list[Available]:
        raise NotImplementedError

    def fetch(self, bbox, year, out_path: Path, cache_dir: Path | None = None) -> Path:
        raise NotImplementedError

    def numeric_year(self, bbox, year, cache_dir: Path | None = None) -> int:
        """The year as a number, with 'latest' turned into the newest year this source has here."""
        year = parse_year(year)
        if year != LATEST:
            return year
        years = [a.year for a in self.available(bbox, cache_dir) if a.year != LATEST]
        if not years:
            raise RuntimeError(f"{self.name} has no imagery here.")
        return max(years)

    def has_nir(self) -> bool:
        """Whether any year here comes with near infrared."""
        return False

    def describe(self) -> dict:
        return {"key": self.key, "name": self.name, "res_m": self.res_m, "pixel": self.pixel_note, "years": self.years_note,
                "nir": self.nir_note, "has_nir": self.has_nir(), "license": self.license, "max_km2": self.max_km2,
                "coverage": list(self.coverage)}


# WMS sources -----------------------------------------------------------------

@dataclass
class WMSLayer:
    url: str
    name: str
    res_m: float


@dataclass(eq=False)
class WMSSource(Source):
    """A national orthophoto service speaking WMS, one layer per year."""

    key: str = ""
    name: str = ""
    coverage: tuple = (-180.0, -90.0, 180.0, 90.0)
    crs: str = "EPSG:3857"
    res_m: float = 0.25
    candidate_years: list = field(default_factory=list)
    rgb: Callable | None = None  # year -> WMSLayer | None
    nir: Callable | None = None  # year -> WMSLayer | None, NIR in channel ``nir_channel``
    nir_channel: int = 0
    pixel_note: str = ""
    years_note: str = ""
    nir_note: str = ""
    license: str = ""
    citation: str = ""
    max_km2: float = 0.4
    max_px: int = 2000
    _probe_cache: dict = field(default_factory=dict, repr=False)

    def has_nir(self) -> bool:
        return self.nir is not None

    def _project(self, bbox):
        from rasterio.warp import transform_bounds

        return transform_bounds("EPSG:4326", self.crs, *bbox)

    def _getmap(self, layer: WMSLayer, bbox_crs, w: int, h: int) -> np.ndarray:
        """RGBA uint8 array for one request. Transparent or blank means no imagery there."""
        from PIL import Image

        params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": layer.name, "STYLES": "",
                  "CRS": self.crs, "BBOX": ",".join(f"{v:.3f}" for v in bbox_crs), "WIDTH": str(w), "HEIGHT": str(h),
                  "FORMAT": "image/png", "TRANSPARENT": "TRUE"}
        raw = _get(f"{layer.url}?{urllib.parse.urlencode(params)}", timeout=120)
        if raw[:1] == b"<":
            raise RuntimeError(f"{self.name} refused layer {layer.name}: {raw[:200].decode('utf-8', 'replace')}")
        return np.asarray(Image.open(io.BytesIO(raw)).convert("RGBA"))

    @staticmethod
    def _blank(a: np.ndarray) -> bool:
        """No imagery: mostly transparent, or one flat color (services paint gaps white)."""
        return (a[..., 3] < 10).mean() > 0.9 or a[..., :3].astype(float).std() < 2.0

    def _probe(self, layer: WMSLayer | None, bbox) -> bool:
        if layer is None:
            return False
        key = (layer.url, layer.name, tuple(round(v, 4) for v in bbox))
        if key not in self._probe_cache:
            x0, y0, x1, y1 = self._project(bbox)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            try:
                a = self._getmap(layer, (cx - 30, cy - 30, cx + 30, cy + 30), 32, 32)
                self._probe_cache[key] = not self._blank(a)
            except Exception as exc:
                log.debug("probe %s %s failed: %s", self.key, layer.name, exc)
                self._probe_cache[key] = False
        return self._probe_cache[key]

    def available(self, bbox, cache_dir: Path | None = None) -> list[Available]:
        """Ask the service which years have pixels here, one tiny request per candidate year."""

        def check(y):
            rgb = self.rgb(y)
            if not self._probe(rgb, bbox):
                return None
            return Available(y, self._probe(self.nir(y) if self.nir else None, bbox), rgb.res_m)

        with ThreadPoolExecutor(max_workers=8) as pool:
            found = [a for a in pool.map(check, list(self.candidate_years)) if a is not None]
        return sorted(found, key=lambda a: (a.year == LATEST, a.year if a.year != LATEST else 0))

    def fetch(self, bbox, year, out_path: Path, cache_dir: Path | None = None) -> Path:
        year = parse_year(year)
        rgb_layer = self.rgb(year)
        if rgb_layer is None:
            raise RuntimeError(f"{self.name} has no layer for {year}.")
        nir_layer = self.nir(year) if self.nir else None
        if nir_layer is not None and not self._probe(nir_layer, bbox):
            log.warning("%s has no near infrared for %s here. Color only.", self.name, year)
            nir_layer = None
        res = rgb_layer.res_m
        grid = grid_from_bounds(self.crs, *self._project(bbox), res)
        left, top = grid.transform.c, grid.transform.f
        bands = 4 if nir_layer else 3
        out = np.zeros((bands, grid.height, grid.width), dtype=np.uint8)
        n_tiles = math.ceil(grid.height / self.max_px) * math.ceil(grid.width / self.max_px)
        log.info("%s %s at %.2f m: %d x %d px in %d tile(s)", self.name, year, res, grid.width, grid.height, n_tiles)
        for r0 in range(0, grid.height, self.max_px):
            for c0 in range(0, grid.width, self.max_px):
                h = min(self.max_px, grid.height - r0)
                w = min(self.max_px, grid.width - c0)
                tb = (left + c0 * res, top - (r0 + h) * res, left + (c0 + w) * res, top - r0 * res)
                a = self._getmap(rgb_layer, tb, w, h)
                # Some services paint missing coverage solid white instead of
                # transparent. A whole tile of one flat color is no imagery,
                # not a very boring photograph.
                empty = np.ones(a.shape[:2], bool) if self._blank(a) else a[..., 3] < 10
                out[:3, r0:r0 + h, c0:c0 + w] = np.where(empty[None], 0, a[..., :3].transpose(2, 0, 1))
                if nir_layer is not None:
                    n = self._getmap(nir_layer, tb, w, h)
                    out[3, r0:r0 + h, c0:c0 + w] = np.where(empty, 0, n[..., self.nir_channel])
        if not out[:3].any():
            raise RuntimeError(f"{self.name} returned no imagery for {year} here.")
        names = ["red", "green", "blue", "nir"][:bands]
        return _write_geotiff(out_path, out, grid.crs, grid.transform, names, nodata=0)


def _pdok_rgb(y):
    base = "https://service.pdok.nl/hwh/luchtfotorgb/wms/v1_0"
    if y == LATEST:
        return WMSLayer(base, "Actueel_orthoHR", 0.08)
    return WMSLayer(base, f"{y}_orthoHR", 0.08) if y >= 2021 else WMSLayer(base, f"{y}_ortho25", 0.25)


def _pdok_nir(y):
    base = "https://service.pdok.nl/hwh/luchtfotocir/wms/v1_0"
    if y == LATEST:
        return WMSLayer(base, "Actueel_orthoHRIR", 0.08)
    if y >= 2025:
        return WMSLayer(base, f"{y}_orthoHRIR", 0.08)
    return WMSLayer(base, f"{y}_ortho25IR", 0.25) if y != 2021 else None


IGN = "https://data.geopf.fr/wms-r/wms"
PNOA_HIST = "https://www.ign.es/wms/pnoa-historico"
PNOA_NOW = "https://www.ign.es/wms-inspire/pnoa-ma"
NRW = "https://www.wms.nrw.de/geobasis/wms_nw_dop"
NRW_HIST = "https://www.wms.nrw.de/geobasis/wms_nw_hist_dop"
BAYERN = "https://geoservices.bayern.de/od/wms/dop/v1/dop40"

PDOK = WMSSource(
    key="pdok", name="PDOK aerial photos (Netherlands)", coverage=(3.2, 50.7, 7.3, 53.6), crs="EPSG:28992", res_m=0.08,
    candidate_years=list(range(2016, 2027)) + [LATEST], rgb=_pdok_rgb, nir=_pdok_nir, nir_channel=0,
    pixel_note="8 cm from 2021, 25 cm before", years_note="every year",
    nir_note="most years (8 cm from 2025, 25 cm before)", license="CC BY 4.0",
    citation="Beeldmateriaal Nederland via PDOK (open data, CC BY 4.0)", max_km2=0.4)

IGN_FR = WMSSource(
    key="ign-fr", name="IGN orthophotos (France)", coverage=(-5.3, 41.3, 9.7, 51.2), crs="EPSG:2154", res_m=0.2,
    candidate_years=list(range(2001, 2025)) + [LATEST],
    rgb=lambda y: WMSLayer(IGN, "ORTHOIMAGERY.ORTHOPHOTOS" if y == LATEST else f"ORTHOIMAGERY.ORTHOPHOTOS{y}", 0.2),
    nir=lambda y: (WMSLayer(IGN, "ORTHOIMAGERY.ORTHOPHOTOS.IRC" if y == LATEST else f"ORTHOIMAGERY.ORTHOPHOTOS.IRC.{y}", 0.2)
                   if y == LATEST or y >= 2012 else None),
    nir_channel=0, pixel_note="20 cm", years_note="2001 on, per departement",
    nir_note="2012 on (infrared color, NIR in the red channel)", license="Licence Ouverte Etalab 2.0",
    citation="IGN France, BD ORTHO and ORTHO HR (Licence Ouverte Etalab 2.0)", max_km2=0.6)

PNOA_ES = WMSSource(
    key="pnoa-es", name="PNOA orthophotos (Spain)", coverage=(-9.5, 35.9, 4.4, 43.9), crs="EPSG:25830", res_m=0.25,
    candidate_years=list(range(2004, 2025)) + [LATEST],
    rgb=lambda y: (WMSLayer(PNOA_NOW, "OI.OrthoimageCoverage", 0.25) if y == LATEST
                   else WMSLayer(PNOA_HIST, f"PNOA{y}", 0.5 if y < 2014 else 0.25)),
    nir=None, pixel_note="25 cm (50 cm before 2014)", years_note="2004 on, per region", nir_note="no",
    license="CC BY 4.0",
    citation="PNOA, (c) Instituto Geografico Nacional de Espana (CC BY 4.0, scne.es)", max_km2=1.5)

NRW_DE = WMSSource(
    key="nrw-de", name="Geobasis NRW orthophotos (Germany, North Rhine-Westphalia)", coverage=(5.85, 50.3, 9.5, 52.55),
    crs="EPSG:25832", res_m=0.1, candidate_years=list(range(2000, 2025)) + [LATEST],
    rgb=lambda y: WMSLayer(NRW, "nw_dop_rgb", 0.1) if y == LATEST else WMSLayer(NRW_HIST, f"nw_hist_dop_{y}", 0.2),
    nir=lambda y: WMSLayer(NRW, "nw_dop_nir", 0.1) if y == LATEST else None,
    nir_channel=0, pixel_note="10 cm latest, 20 cm history", years_note="2000 on", nir_note="latest flight",
    license="Datenlizenz Deutschland Zero 2.0",
    citation="Geobasis NRW (Datenlizenz Deutschland Zero 2.0)", max_km2=0.4)

BAYERN_DE = WMSSource(
    key="bayern-de", name="Bayerische Vermessungsverwaltung DOP40 (Germany, Bavaria)", coverage=(8.95, 47.25, 13.85, 50.6),
    crs="EPSG:25832", res_m=0.4, candidate_years=[LATEST],
    rgb=lambda y: WMSLayer(BAYERN, "by_dop40c", 0.4) if y == LATEST else None, nir=None, pixel_note="40 cm",
    years_note="latest only", nir_note="no",
    license="CC BY 4.0", citation="Bayerische Vermessungsverwaltung, DOP40 (CC BY 4.0)", max_km2=3.0)


# NAIP --------------------------------------------------------------------------

PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
PC_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}"
NAIP_CITATION = "USDA Farm Service Agency, National Agriculture Imagery Program (public domain), via Microsoft Planetary Computer"
PDOK_CITATION = PDOK.citation


def naip_items(bbox_lonlat) -> list:
    from pystac_client import Client

    items = list(Client.open(PC_STAC).search(collections=["naip"], bbox=bbox_lonlat, max_items=500).items())
    return sorted(items, key=lambda i: i.datetime or i.properties.get("start_datetime"))


def naip_years(bbox_lonlat) -> list[int]:
    return sorted({it.datetime.year for it in naip_items(bbox_lonlat)})


class NAIPSource(Source):
    key = "naip"
    name = "NAIP (USA)"
    coverage = (-125.0, 24.4, -66.8, 49.5)
    res_m = 0.6
    pixel_note = "60 cm (1 m before 2016)"
    years_note = "every 2 to 3 years, per state"
    nir_note = "yes"
    license = "public domain"
    citation = NAIP_CITATION
    max_km2 = 6.0

    def has_nir(self) -> bool:
        return True

    def available(self, bbox, cache_dir=None) -> list[Available]:
        out = {}
        for it in naip_items(bbox):
            y = it.datetime.year
            out[y] = Available(y, True, float(it.properties.get("gsd", 1.0)), f"{y} ({it.datetime.date().isoformat()})")
        return [out[y] for y in sorted(out)]

    def fetch(self, bbox, year, out_path: Path, cache_dir=None) -> Path:
        return fetch_naip(bbox, self.numeric_year(bbox, year, cache_dir), out_path)


def fetch_naip(bbox_lonlat, year: int, out_path: Path) -> Path:
    """Mosaic NAIP for one year over the bbox into a 4-band (R, G, B, NIR) uint8 GeoTIFF."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import transform_bounds

    from dgd.aoi import utm_epsg

    items = [it for it in naip_items(bbox_lonlat) if it.datetime.year == year]
    if not items:
        raise RuntimeError(f"No NAIP imagery for {year} here. Years available: {naip_years(bbox_lonlat)}")
    token = json.loads(_get(PC_TOKEN.format(collection="naip")))["token"]
    gsd = min(float(it.properties.get("gsd", 1.0)) for it in items)
    lon, lat = (bbox_lonlat[0] + bbox_lonlat[2]) / 2, (bbox_lonlat[1] + bbox_lonlat[3]) / 2
    crs = f"EPSG:{utm_epsg(lon, lat)}"
    grid = grid_from_bounds(crs, *transform_bounds("EPSG:4326", crs, *bbox_lonlat), gsd)
    out = np.zeros((4, grid.height, grid.width), dtype=np.uint8)
    for it in items:
        href = it.assets["image"].href + "?" + token
        with rasterio.open(href) as src, WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                                                   height=grid.height, resampling=Resampling.bilinear) as vrt:
            arr = vrt.read([1, 2, 3, 4])
        fill = (out.max(axis=0) == 0) & (arr.max(axis=0) > 0)
        out[:, fill] = arr[:, fill]
    if not out.any():
        raise RuntimeError("NAIP tiles were found but none of them cover the area. Try a slightly bigger box.")
    log.info("NAIP %s: %d tile(s), %.1f m", year, len(items), gsd)
    return _write_geotiff(out_path, out, grid.crs, grid.transform, ["red", "green", "blue", "nir"], nodata=0)


def fetch_pdok(bbox_lonlat, year, out_path: Path, res: float = 0.08, with_nir: bool = True) -> Path:
    """Kept for scripts written against the first version. Same as PDOK.fetch."""
    return PDOK.fetch(bbox_lonlat, year, out_path)


# New Zealand, LINZ -------------------------------------------------------------

LINZ_BUCKET = "https://nz-imagery.s3.ap-southeast-2.amazonaws.com"
LINZ_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # map sheet letters skip I and O
LINZ_ROW0 = LINZ_LETTERS.index("A") * 24 + LINZ_LETTERS.index("S")  # sheet row "AS" sits at the top


def linz_sheet_origin(sheet: str) -> tuple[float, float]:
    """Top-left corner (NZTM2000) of a Topo50 map sheet such as 'BX24'.

    Fitted against published tiles at five scales, not taken on faith: sheets
    are 24 km by 36 km, columns step east from 988000, rows step south from
    6234000 starting at 'AS'.
    """
    r = LINZ_LETTERS.index(sheet[0]) * 24 + LINZ_LETTERS.index(sheet[1])
    return 988000.0 + 24000.0 * int(sheet[2:]), 6234000.0 - 36000.0 * (r - LINZ_ROW0)


def linz_tile_bounds(name: str) -> tuple[float, float, float, float]:
    """(left, bottom, right, top) in NZTM2000 of a tile such as 'BX24_500_038006'."""
    sheet, scale, code = name.split("_")
    per = 50000 // int(scale)
    tw, th = 24000.0 / per, 36000.0 / per
    half = len(code) // 2
    row, col = int(code[:half]), int(code[half:])
    sl, st = linz_sheet_origin(sheet)
    left, top = sl + (col - 1) * tw, st - (row - 1) * th
    return left, top - th, left + tw, top


def linz_tiles(bbox_nztm, scale: int) -> list[str]:
    """Names of every tile at ``scale`` touching an NZTM2000 box."""
    per = 50000 // scale
    tw, th = 24000.0 / per, 36000.0 / per
    digits = 3 if per > 99 else 2
    x0, y0, x1, y1 = bbox_nztm
    names = []
    for col_g in range(int(math.floor((x0 - 988000) / tw)), int(math.floor((x1 - 988000) / tw)) + 1):
        for row_g in range(int(math.floor((6234000 - y1) / th)), int(math.floor((6234000 - y0) / th)) + 1):
            s_col, t_col = divmod(col_g, per)
            s_row, t_row = divmod(row_g, per)
            r = LINZ_ROW0 + s_row
            if s_col < 1 or r < 0 or r >= len(LINZ_LETTERS) * 24:
                continue
            sheet = f"{LINZ_LETTERS[r // 24]}{LINZ_LETTERS[r % 24]}{s_col:02d}"
            names.append(f"{sheet}_{scale}_{t_row + 1:0{digits}d}{t_col + 1:0{digits}d}")
    return names


class LINZSource(Source):
    key = "linz-nz"
    name = "LINZ aerial imagery (New Zealand)"
    coverage = (166.0, -47.5, 178.7, -34.0)
    res_m = 0.075
    pixel_note = "5 to 12.5 cm in towns, 20 to 40 cm rural, older scans coarser"
    years_note = "often decades of flights"
    nir_note = "no"
    license = "CC BY 4.0"
    citation = "Toitu Te Whenua Land Information New Zealand (CC BY 4.0)"
    max_km2 = 0.6

    def index(self, cache_dir: Path | None) -> list[dict]:
        """Every imagery collection with its box, dates, pixel size and tile scale.

        Built from two small byte-range reads per collection (title and first
        tile at the top of each file, extent and pixel size at the bottom),
        because the full files run to megabytes of tile links. Cached a month.
        """
        cache = Path(cache_dir) / "linz" / "index.json" if cache_dir else None
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < 30 * 86400:
            return json.loads(cache.read_text())
        cat = json.loads(_get(f"{LINZ_BUCKET}/catalog.json"))
        hrefs = [link["href"][2:] for link in cat["links"] if link["rel"] == "child"]

        def one(href):
            url = f"{LINZ_BUCKET}/{href}"
            try:
                head = _get(url, headers={"Range": "bytes=0-4095"}).decode("utf-8", "replace")
                tail = _get(url, headers={"Range": "bytes=-4096"}).decode("utf-8", "replace")
            except Exception as exc:
                log.debug("LINZ %s: %s", href, exc)
                return None
            return parse_linz_collection(href, head, tail)

        log.info("Indexing %d LINZ imagery collections (once a month, about a minute)", len(hrefs))
        with ThreadPoolExecutor(max_workers=24) as pool:
            idx = [c for c in pool.map(one, hrefs) if c]
        if cache:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(idx))
        return idx

    def _collections(self, bbox, cache_dir):
        out = []
        for c in self.index(cache_dir):
            b = c["bbox"]
            if b[0] <= bbox[2] and b[2] >= bbox[0] and b[1] <= bbox[3] and b[3] >= bbox[1]:
                out.append(c)
        return out

    def available(self, bbox, cache_dir=None) -> list[Available]:
        best: dict[int, dict] = {}
        for c in self._collections(bbox, cache_dir):
            y = c["end"]
            if y not in best or (c["gsd"] or 9) < (best[y]["gsd"] or 9):
                best[y] = c
        return [Available(y, False, c["gsd"] or 0.3, f"{y}: {c['title']}") for y, c in sorted(best.items())]

    def fetch(self, bbox, year, out_path: Path, cache_dir=None) -> Path:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.vrt import WarpedVRT
        from rasterio.warp import transform_bounds

        year = self.numeric_year(bbox, year, cache_dir)
        cands = sorted((c for c in self._collections(bbox, cache_dir) if c["end"] == year), key=lambda c: c["gsd"] or 9)
        if not cands:
            raise RuntimeError(f"No LINZ imagery for {year} here.")
        nztm = transform_bounds("EPSG:4326", "EPSG:2193", *bbox)
        for c in cands:
            res = max(c["gsd"] or 0.3, 0.05)
            grid = grid_from_bounds("EPSG:2193", *nztm, res)
            out = np.zeros((3, grid.height, grid.width), dtype=np.uint8)
            got = 0
            for name in linz_tiles(nztm, c["scale"]):
                url = f"{LINZ_BUCKET}/{c['path']}/{name}.tiff"
                try:
                    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(url) as src, WarpedVRT(
                            src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                            resampling=Resampling.bilinear) as vrt:
                        arr = vrt.read([1, 2, 3] if src.count >= 3 else [1, 1, 1])
                except Exception as exc:
                    log.debug("LINZ tile %s: %s", name, exc)
                    continue
                fill = (out.max(axis=0) == 0) & (arr.max(axis=0) > 0)
                out[:, fill] = arr[:, fill]
                got += 1
            if got and out.any():
                log.info("LINZ %s: %s, %d tile(s) at %.3f m", year, c["title"], got, res)
                self.citation = f"Toitu Te Whenua Land Information New Zealand, {c['title']} (CC BY 4.0)"
                return _write_geotiff(out_path, out, grid.crs, grid.transform, ["red", "green", "blue"], nodata=0)
        raise RuntimeError(f"LINZ lists {year} imagery near here, but no tile covers the box itself.")


def parse_linz_collection(href: str, head: str, tail: str) -> dict | None:
    """Pull what the index needs out of the first and last few KB of a collection file."""
    title = re.search(r'"title":\s*"([^"]+)"', head)
    first = re.search(r'"href":\s*"\./([A-Z]{2}\d{2}_(\d+)_\d+)\.json"', head)
    bbox = re.search(r'"bbox":\s*\[\s*\[([-\d.,\s]+)\]', tail)
    interval = re.search(r'"interval":\s*\[\s*\[\s*"(\d{4})[^"]*",\s*"(\d{4})', tail)
    gsd = re.search(r'"gsd":\s*([\d.]+)', tail)
    category = re.search(r'"linz:geospatial_category":\s*"([^"]+)"', tail)
    if not (first and bbox and interval):
        return None
    return {"path": href.rsplit("/", 1)[0], "title": title.group(1) if title else href,
            "scale": int(first.group(2)), "bbox": [float(v) for v in bbox.group(1).split(",")],
            "start": int(interval.group(1)), "end": int(interval.group(2)),
            "gsd": float(gsd.group(1)) if gsd else None, "category": category.group(1) if category else ""}


# OpenAerialMap -----------------------------------------------------------------

OAM_API = "https://api.openaerialmap.org/meta"


class OAMSource(Source):
    key = "oam"
    name = "OpenAerialMap (anywhere, patchy)"
    coverage = (-180.0, -85.0, 180.0, 85.0)
    res_m = 0.1
    pixel_note = "1 to 30 cm, drones and satellites"
    years_note = "whatever somebody uploaded"
    nir_note = "rarely"
    license = "per image, usually CC BY 4.0"
    citation = "OpenAerialMap contributors (licence per image, usually CC BY 4.0)"
    max_km2 = 1.0

    def search(self, bbox) -> list[dict]:
        out, page = [], 1
        while page <= 5:
            q = urllib.parse.urlencode({"bbox": ",".join(f"{v:.6f}" for v in bbox), "limit": 100, "page": page})
            res = json.loads(_get(f"{OAM_API}?{q}")).get("results", [])
            out += res
            if len(res) < 100:
                break
            page += 1
        return [r for r in out if str(r.get("uuid", "")).lower().endswith((".tif", ".tiff"))]

    @staticmethod
    def gsd_m(r) -> float | None:
        g = r.get("gsd")
        if not isinstance(g, (int, float)) or g <= 0:
            return None
        return float(g) * 111320 if g < 0.001 else float(g)  # some entries come in degrees

    def footprints(self, bbox) -> list[dict]:
        """One GeoJSON feature per image touching the box, for drawing on a map."""
        feats = []
        for r in self.search(bbox):
            geom = r.get("geojson")
            if not isinstance(geom, dict) or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            day = str(r.get("acquisition_start", ""))[:10]
            feats.append({"type": "Feature", "geometry": {"type": geom["type"], "coordinates": geom.get("coordinates")},
                          "properties": {"id": str(r.get("_id") or r.get("uuid")), "title": str(r.get("title", ""))[:80],
                                         "provider": str(r.get("provider", ""))[:60], "platform": str(r.get("platform", "")),
                                         "date": day, "res_m": self.gsd_m(r),
                                         "license": str((r.get("properties") or {}).get("license") or "see OpenAerialMap")}})
        return feats

    def available(self, bbox, cache_dir=None) -> list[Available]:
        best: dict[int, dict] = {}
        for r in self.search(bbox):
            y = int(str(r.get("acquisition_start", ""))[:4] or 0) if str(r.get("acquisition_start", ""))[:4].isdigit() else 0
            if y < 1990:
                continue
            if y not in best or (self.gsd_m(r) or 9) < (self.gsd_m(best[y]) or 9):
                best[y] = r
        return [Available(y, False, self.gsd_m(r) or 0.1, f"{y}: {str(r.get('title', ''))[:60]} ({str(r.get('provider', ''))[:40]})")
                for y, r in sorted(best.items())]

    def fetch(self, bbox, year, out_path: Path, cache_dir=None) -> Path:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.vrt import WarpedVRT
        from rasterio.warp import transform_bounds

        from dgd.aoi import utm_epsg

        year = self.numeric_year(bbox, year, cache_dir)
        imgs = [r for r in self.search(bbox) if str(r.get("acquisition_start", ""))[:4] == str(year)]
        if not imgs:
            raise RuntimeError(f"No OpenAerialMap imagery from {year} here.")
        imgs.sort(key=lambda r: self.gsd_m(r) or 9)
        res = max(self.gsd_m(imgs[0]) or 0.1, 0.05)
        lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
        crs = f"EPSG:{utm_epsg(lon, lat)}"
        grid = grid_from_bounds(crs, *transform_bounds("EPSG:4326", crs, *bbox), res)
        out = np.zeros((3, grid.height, grid.width), dtype=np.uint8)
        used = []
        for r in imgs:
            try:
                with rasterio.open(r["uuid"]) as src, WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width,
                                                                 height=grid.height, resampling=Resampling.bilinear) as vrt:
                    arr = vrt.read([1, 2, 3] if src.count >= 3 else [1, 1, 1])
            except Exception as exc:
                log.debug("OAM image %s: %s", r.get("uuid"), exc)
                continue
            fill = (out.max(axis=0) == 0) & (arr.max(axis=0) > 0)
            if fill.any():
                out[:, fill] = arr[:, fill]
                lic = (r.get("properties") or {}).get("license") or "see OpenAerialMap"
                used.append(f"{r.get('title', '')} by {r.get('provider', '')}, licence {lic}")
        if not used:
            raise RuntimeError(f"OpenAerialMap lists {year} imagery here but none of it could be read.")
        self.citation = "OpenAerialMap: " + "; ".join(used)
        return _write_geotiff(out_path, out, grid.crs, grid.transform, ["red", "green", "blue"], nodata=0)


SOURCES: dict[str, Source] = {s.key: s for s in (NAIPSource(), PDOK, IGN_FR, PNOA_ES, NRW_DE, BAYERN_DE, LINZSource(), OAMSource())}


def sources_for(bbox) -> list[Source]:
    """Sources whose coverage contains the box. OpenAerialMap always qualifies, it just may have nothing."""
    return [s for s in SOURCES.values() if s.covers(bbox)]


COVERAGE_FILE = Path(__file__).resolve().parent / "data" / "coverage.geojson"


def coverage_geojson() -> dict:
    """Where each national program has flown the whole country or state, with each source's details.

    The latest flight covers all of it. Older years were flown in pieces,
    which is why the year list still asks the service about the exact box.
    Borders are Natural Earth, grown by about a kilometre so coastal towns
    stay in (scripts/build_coverage.py). OpenAerialMap is patchy by nature
    and is asked per area instead, see ``OAMSource.footprints``.
    """
    fc = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
    for f in fc["features"]:
        f["properties"].update(SOURCES[f["properties"]["source"]].describe())
    return fc


__all__ = [
    "LATEST",
    "NAIP_CITATION",
    "PDOK_CITATION",
    "SOURCES",
    "Available",
    "Source",
    "coverage_geojson",
    "fetch_naip",
    "fetch_pdok",
    "linz_tile_bounds",
    "linz_tiles",
    "naip_items",
    "naip_years",
    "parse_linz_collection",
    "parse_year",
    "sources_for",
]
