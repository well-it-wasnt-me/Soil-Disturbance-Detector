"""Roads, tracks, paths, buildings and water from OpenStreetMap.

Two uses. The access prior: people who bury bodies do not carry them far,
so distance to the nearest mapped road, track or path is a ranking hint.
And the obvious exclusions: nobody buries anyone on a roof or in the middle
of a paved road, yet roofs and parked cars are what a change detector loves
most. Masking them costs nothing and removes a whole class of false alarms.

Where the map is thin the prior is weak and the masks are holey. The report
says how far the nearest mapped access was, so nobody mistakes "unmapped"
for "remote".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

from dgd.aoi import Grid

log = logging.getLogger(__name__)

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
OSM_CITATION = "Roads, paths and buildings (c) OpenStreetMap contributors, ODbL"
USER_AGENT = "Soil-Disturbance-Detector (free tool for law enforcement)"

# Half-widths of paved road surfaces, metres. Tracks, paths and footways are
# left alone: a grave two metres off a forest track is exactly the kind of
# place worth a look.
PAVED_HALF_WIDTH = {
    "motorway": 8.0, "trunk": 7.0, "primary": 6.0, "secondary": 5.0, "tertiary": 4.0,
    "unclassified": 3.0, "residential": 3.0, "service": 2.0, "living_street": 2.5,
    "motorway_link": 4.0, "trunk_link": 4.0, "primary_link": 4.0, "secondary_link": 3.5, "tertiary_link": 3.0,
}


def overpass_urls() -> list[str]:
    """Servers to try, in order. A self-hosted one (DGD_OVERPASS_URL) goes first."""
    own = os.environ.get("DGD_OVERPASS_URL", "").strip()
    return ([own] if own else []) + [u for u in OVERPASS_URLS if u != own]


def _partial(data: dict) -> str | None:
    """The reason an Overpass answer is incomplete, or None if it is whole.

    A query that times out or runs out of memory still comes back as HTTP
    200, with whatever was found so far and a ``remark`` saying so.
    """
    remark = str(data.get("remark") or "")
    low = remark.lower()
    if "error" in low or "timed out" in low or "out of memory" in low:
        return remark
    return None


def _overpass(query: str, timeout: int) -> dict:
    last = None
    for url in overpass_urls():
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode(),
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout + 30) as resp:
                data = json.loads(resp.read())
            why = _partial(data)
            if why:
                raise RuntimeError(f"incomplete answer ({why})")
            return data
        except Exception as exc:  # busy server, mirror down, the usual internet weather
            log.info("Overpass %s failed: %s", url, exc)
            last = exc
    raise RuntimeError(f"every Overpass server failed, last error: {last}")


def fetch_osm(bbox_lonlat: tuple[float, float, float, float], cache_dir: Path | None = None, timeout: int = 90) -> dict:
    """Highways (with their type) and building outlines in the bbox, lon/lat coordinates."""
    min_lon, min_lat, max_lon, max_lat = bbox_lonlat
    b = f"({min_lat},{min_lon},{max_lat},{max_lon})"
    query = (f'[out:json][timeout:{timeout}];(way["highway"]{b};way["building"]{b};way["natural"="water"]{b};'
             f'way["landuse"="reservoir"]{b};way["waterway"="riverbank"]{b};way["amenity"="parking"]{b};'
             f'way["area:highway"]{b};);out geom;')
    key = hashlib.sha1(query.encode()).hexdigest()[:16]
    cache = Path(cache_dir) / "osm" / f"{key}.json" if cache_dir else None
    data = json.loads(cache.read_text()) if cache is not None and cache.exists() else None
    if data is not None and _partial(data):
        # Written by an older version that cached incomplete answers. Ask again.
        data = None
    if data is None:
        data = _overpass(query, timeout)
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data))
    ways, buildings, water, paved = [], [], [], []
    for el in data.get("elements", []):
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [(p["lon"], p["lat"]) for p in geom]
        tags = el.get("tags", {})
        closed = len(coords) >= 4 and coords[0] == coords[-1]
        if "building" in tags and len(coords) >= 4:
            buildings.append(coords)
        elif closed and (tags.get("amenity") == "parking" or "area:highway" in tags
                         or ("highway" in tags and tags.get("area") == "yes")):
            # Car parks and paved plazas: parked cars and lamp-post shadows are
            # the favorite false alarm of a change detector, and nobody digs there.
            paved.append(coords)
        elif "highway" in tags:
            ways.append((tags["highway"], coords))
        elif len(coords) >= 4 and (tags.get("natural") == "water" or tags.get("landuse") == "reservoir" or tags.get("waterway") == "riverbank"):
            water.append(coords)
    return {"ways": ways, "buildings": buildings, "water": water, "paved": paved}


_HSTORE = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')


def _rings(geom) -> list:
    """Exterior rings of a (multi)polygon as lon/lat coordinate lists. Holes are ignored, which for masking is fine."""
    parts = getattr(geom, "geoms", [geom])
    return [list(p.exterior.coords) for p in parts if p is not None and not p.is_empty and hasattr(p, "exterior")]


def load_osm_file(path: str, bbox_lonlat: tuple[float, float, float, float]) -> dict:
    """Same features as ``fetch_osm``, read offline from a local OpenStreetMap extract.

    Any ``.osm.pbf`` or ``.osm`` file GDAL can read: a country or province
    download from Geofabrik, or a clip made with osmium. No server, no rate
    limit, works on a laptop in a field or on a network that has never heard
    of the internet. Use the smallest extract that covers the area, because
    GDAL reads the whole file to find the bit you asked for.
    """
    import pyogrio

    ways, buildings, water, paved = [], [], [], []
    lines = pyogrio.read_dataframe(path, layer="lines", bbox=bbox_lonlat, columns=["highway"])
    for kind, geom in zip(lines["highway"], lines.geometry):
        if isinstance(kind, str) and kind and geom is not None and not geom.is_empty:
            for part in getattr(geom, "geoms", [geom]):
                ways.append((kind, list(part.coords)))
    cols = ["building", "amenity", "landuse", "natural", "other_tags"]
    polys = pyogrio.read_dataframe(path, layer="multipolygons", bbox=bbox_lonlat, columns=cols)
    def val(row, key) -> str:
        # Missing tags come back as NaN, and NaN is truthy. Ask me how I know.
        v = row.get(key)
        return v if isinstance(v, str) else ""

    for _, row in polys.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        other = dict(_HSTORE.findall(val(row, "other_tags")))
        if val(row, "building"):
            buildings += _rings(geom)
        elif val(row, "amenity") == "parking" or "area:highway" in other or "highway" in other:
            paved += _rings(geom)
        elif val(row, "natural") == "water" or val(row, "landuse") == "reservoir" or other.get("waterway") == "riverbank":
            water += _rings(geom)
    log.info("OSM file %s: %d ways, %d buildings, %d paved areas, %d water bodies", path, len(ways), len(buildings), len(paved), len(water))
    return {"ways": ways, "buildings": buildings, "water": water, "paved": paved}


def fetch_ways(bbox_lonlat, cache_dir: Path | None = None, timeout: int = 90) -> list:
    """Just the highway geometries, for callers that only need access distance."""
    return [coords for _, coords in fetch_osm(bbox_lonlat, cache_dir, timeout)["ways"]]


def _project(grid: Grid, coords):
    from pyproj import Transformer

    tr = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
    xs, ys = tr.transform([c[0] for c in coords], [c[1] for c in coords])
    return list(zip(xs, ys))


def distance_to_ways(grid: Grid, lines: list) -> np.ndarray:
    """Distance in metres from every pixel to the nearest way. Inf if there are none."""
    from rasterio.features import rasterize
    from scipy.ndimage import distance_transform_edt
    from shapely.geometry import LineString, box

    if not lines:
        return np.full(grid.shape, np.inf, dtype=np.float32)
    geoms = [LineString(_project(grid, coords)) for coords in lines]
    burned = rasterize(((g, 1) for g in geoms), out_shape=grid.shape, transform=grid.transform, fill=0,
                       all_touched=True, dtype="uint8")
    if not burned.any():
        d = min(box(*grid.bounds).distance(g) for g in geoms)
        log.info("Nearest mapped way is %.0f m outside the area.", d)
        return np.full(grid.shape, d, dtype=np.float32)
    return (distance_transform_edt(burned == 0) * grid.res).astype(np.float32)


def built_mask(grid: Grid, osm: dict) -> np.ndarray:
    """True on mapped buildings, paved roads, car parks, plazas and open water. Places nobody digs a grave."""
    from rasterio.features import rasterize
    from shapely.geometry import LineString, Polygon

    shapes = []
    for coords in osm.get("buildings", []) + osm.get("water", []) + osm.get("paved", []):
        poly = Polygon(_project(grid, coords))
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.area > 0:
            shapes.append((poly.buffer(0.5), 1))
    for kind, coords in osm.get("ways", []):
        half = PAVED_HALF_WIDTH.get(kind)
        if half:
            shapes.append((LineString(_project(grid, coords)).buffer(half), 1))
    if not shapes:
        return np.zeros(grid.shape, dtype=bool)
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform, fill=0, dtype="uint8").astype(bool)


__all__ = ["OSM_CITATION", "built_mask", "distance_to_ways", "fetch_osm", "fetch_ways", "load_osm_file", "overpass_urls"]
