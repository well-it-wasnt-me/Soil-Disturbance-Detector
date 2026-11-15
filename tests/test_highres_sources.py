import numpy as np
import pytest
from pyproj import Transformer

from dgd import highres_sources as hs

# Real LINZ tiles and their published WGS84 boxes, the ones the grid formula was fitted on.
LINZ_SAMPLES = {
    "BX24_1000_1405": (172.5782, -43.5329, 172.5842, -43.5264),
    "BH31_500_045099": (174.7968, -39.035, 174.7996, -39.0317),
    "CE18_10000_0201": (170.683, -45.8189, 170.7473, -45.7529),
    "BF41_5000_0404": (177.3341, -38.3036, 177.3634, -38.2702),
    "BY25_500_021019": (172.9045, -43.8351, 172.9075, -43.8318),
}


@pytest.mark.parametrize("name, wgs", LINZ_SAMPLES.items())
def test_linz_tile_math_matches_published_tiles(name, wgs):
    # A tile is a square in NZTM2000. Its published box is the lon/lat envelope
    # of that square, so project the square out and compare envelopes.
    left, bottom, right, top = hs.linz_tile_bounds(name)
    tr = Transformer.from_crs(2193, 4326, always_xy=True)
    lons, lats = tr.transform([left, right, left, right], [bottom, bottom, top, top])
    tol = 2e-4  # the published boxes were copied with four decimals
    assert abs(min(lons) - wgs[0]) < tol and abs(max(lons) - wgs[2]) < tol
    assert abs(min(lats) - wgs[1]) < tol and abs(max(lats) - wgs[3]) < tol
    # and the inverse finds the same tile from a point inside it
    scale = int(name.split("_")[1])
    cx, cy = (left + right) / 2, (bottom + top) / 2
    assert name in hs.linz_tiles((cx - 1, cy - 1, cx + 1, cy + 1), scale)


def test_linz_tiles_cover_a_box_without_gaps():
    left, bottom, right, top = hs.linz_tile_bounds("BX24_500_038006")
    names = hs.linz_tiles((left - 100, bottom - 100, right + 100, top + 100), 500)
    assert len(names) == 9 and "BX24_500_038006" in names


def test_parse_year():
    assert hs.parse_year("2024") == 2024 and hs.parse_year("Latest") == hs.LATEST
    with pytest.raises(ValueError):
        hs.parse_year("24")


def test_parse_linz_collection_from_head_and_tail():
    head = '{"type": "Collection", "title": "Christchurch 0.075m Urban Aerial Photos (2023)", "links": [{"href": "./BW23_500_098098.json", "rel": "item"}'
    tail = ('"extent": {"spatial": {"bbox": [[172.39, -43.83, 173.09, -43.38]]}, "temporal": {"interval": [["2023-01-15T11:00:00Z", '
            '"2023-04-04T12:00:00Z"]]}}, "gsd": 0.075, "linz:geospatial_category": "urban-aerial-photos"')
    c = hs.parse_linz_collection("canterbury/christchurch_2023_0.075m/rgb/2193/collection.json", head, tail)
    assert c["scale"] == 500 and c["end"] == 2023 and c["gsd"] == 0.075 and c["bbox"][0] == 172.39
    assert c["path"] == "canterbury/christchurch_2023_0.075m/rgb/2193"
    assert hs.parse_linz_collection("x", "{}", "{}") is None


def test_oam_gsd_units():
    assert hs.OAMSource.gsd_m({"gsd": 0.05}) == 0.05
    assert hs.OAMSource.gsd_m({"gsd": 1e-6}) == pytest.approx(0.111, abs=0.001)  # some entries are in degrees
    assert hs.OAMSource.gsd_m({"gsd": None}) is None


def test_sources_for_picks_by_coverage():
    keys = lambda bbox: {s.key for s in hs.sources_for(bbox)}  # noqa: E731
    assert keys((4.93, 52.34, 4.94, 52.35)) == {"pdok", "oam"}
    assert keys((1.89, 47.89, 1.91, 47.91)) == {"ign-fr", "oam"}
    assert keys((-117.28, 33.87, -117.26, 33.88)) == {"naip", "oam"}
    assert keys((172.6, -43.54, 172.62, -43.52)) == {"linz-nz", "oam"}
    assert keys((14.39, 41.09, 14.41, 41.10)) == {"oam"}  # Campania: no national service wired up


# Places near the edges, where a simplified border would lie first.
COVERED = {"naip": [(-80.19, 25.77), (-95.0, 40.0)],  # downtown Miami sits on the coast, Kansas does not
           "pdok": [(4.90, 52.37)], "ign-fr": [(2.35, 48.85), (9.15, 42.15)],  # Paris, Corsica
           "pnoa-es": [(-3.70, 40.42), (2.65, 39.57)], "nrw-de": [(6.96, 50.94)],  # Madrid, Mallorca, Cologne
           "bayern-de": [(11.58, 48.14)], "linz-nz": [(174.76, -36.85), (168.66, -45.03)]}
NOT_COVERED = {"naip": [(-149.9, 61.2), (-87.0, 44.0)],  # Anchorage, Lake Michigan
               "pdok": [(4.40, 51.20)], "ign-fr": [(-52.3, 4.9), (4.40, 51.20)],  # Antwerp, Cayenne
               "pnoa-es": [(-9.14, 38.72)], "nrw-de": [(8.68, 50.11)], "bayern-de": [(9.18, 48.78)]}  # Lisbon, Frankfurt, Stuttgart


def test_coverage_footprints_are_where_the_sources_are():
    from shapely.geometry import Point, box, shape

    fc = hs.coverage_geojson()
    zones = {f["properties"]["key"]: shape(f["geometry"]) for f in fc["features"]}
    assert set(zones) == set(hs.SOURCES) - {"oam"}  # OpenAerialMap is patchy, it gets asked per area instead
    for key, zone in zones.items():
        assert zone.is_valid and box(*hs.SOURCES[key].coverage).covers(zone), key
    for key, places in COVERED.items():
        assert all(zones[key].contains(Point(p)) for p in places), key
    for key, places in NOT_COVERED.items():
        assert not any(zones[key].contains(Point(p)) for p in places), key
    props = {f["properties"]["key"]: f["properties"] for f in fc["features"]}
    assert props["pdok"]["has_nir"] and not props["pnoa-es"]["has_nir"] and props["naip"]["pixel"].startswith("60 cm")


def test_oam_footprints_keep_what_the_map_needs(monkeypatch):
    rec = {"_id": "abc", "title": "Field north of the farm", "provider": "Somebody", "platform": "uav",
           "acquisition_start": "2024-05-12T23:00:00.000Z", "gsd": 0.03, "properties": {"license": "CC-BY 4.0"},
           "uuid": "https://example.org/a.tif",
           "geojson": {"type": "MultiPolygon", "bbox": [0, 0, 1, 1], "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}}
    oam = hs.SOURCES["oam"]
    monkeypatch.setattr(oam, "search", lambda bbox: [rec, {**rec, "_id": "no footprint", "geojson": None}])
    feats = oam.footprints((0, 0, 1, 1))
    assert len(feats) == 1 and feats[0]["geometry"] == {"type": "MultiPolygon", "coordinates": rec["geojson"]["coordinates"]}
    assert feats[0]["properties"] == {"id": "abc", "title": "Field north of the farm", "provider": "Somebody", "platform": "uav",
                                      "date": "2024-05-12", "res_m": 0.03, "license": "CC-BY 4.0"}


def _fake_wms(monkeypatch, src, have_nir=True):
    """A fake GetMap: textured imagery for 2023 and 'latest', blank for other years, NIR in channel 0 when asked."""
    rng = np.random.default_rng(0)

    def getmap(layer, bbox, w, h):
        if "2023" not in layer.name and layer.name not in ("ORTHOIMAGERY.ORTHOPHOTOS", "ORTHOIMAGERY.ORTHOPHOTOS.IRC"):
            return np.full((h, w, 4), 255, dtype=np.uint8)
        if "IRC" in layer.name and not have_nir:
            return np.full((h, w, 4), 255, dtype=np.uint8)
        a = rng.integers(0, 255, (h, w, 4), dtype=np.uint8)
        a[..., 3] = 255
        if "IRC" in layer.name:
            a[..., 0] = 200
        return a

    monkeypatch.setattr(src, "_getmap", getmap)
    src._probe_cache.clear()


def test_wms_available_and_fetch_with_nir(monkeypatch, tmp_path):
    import rasterio

    src = hs.IGN_FR
    _fake_wms(monkeypatch, src)
    bbox = (1.899, 47.899, 1.901, 47.901)
    years = src.available(bbox)
    assert [a.year for a in years] == [2023, "latest"] and all(a.nir for a in years)
    p = src.fetch(bbox, 2023, tmp_path / "fr.tif")
    with rasterio.open(p) as ds:
        assert ds.count == 4 and ds.crs.to_epsg() == 2154 and abs(ds.res[0] - 0.2) < 1e-9
        assert (ds.read(4) == 200).all()  # NIR came from channel 0 of the infrared layer
        assert ds.colorinterp[3].name == "undefined"  # and nobody will mistake it for alpha


def test_wms_fetch_falls_back_to_color_without_nir(monkeypatch, tmp_path):
    import rasterio

    src = hs.IGN_FR
    _fake_wms(monkeypatch, src, have_nir=False)
    p = src.fetch((1.899, 47.899, 1.901, 47.901), 2023, tmp_path / "fr.tif")
    with rasterio.open(p) as ds:
        assert ds.count == 3
    with pytest.raises(RuntimeError, match="no imagery|no layer"):
        src.fetch((1.899, 47.899, 1.901, 47.901), 2011, tmp_path / "none.tif")


@pytest.mark.network
@pytest.mark.parametrize("key, lat, lon, year", [("ign-fr", 47.90, 1.90, 2023), ("pnoa-es", 40.45, -3.60, "latest"), ("nrw-de", 50.95, 6.90, "latest")])
def test_live_sources(tmp_path, key, lat, lon, year):
    import rasterio

    from dgd.cli import _bbox_around

    p = hs.SOURCES[key].fetch(_bbox_around(lat, lon, 30), year, tmp_path / f"{key}.tif")
    with rasterio.open(p) as ds:
        assert ds.read(1).std() > 5


def test_latest_turns_into_the_newest_year(monkeypatch):
    src = hs.SOURCES["oam"]
    monkeypatch.setattr(src, "available", lambda bbox, cache_dir=None: [hs.Available(2019, False, 0.1), hs.Available(2023, False, 0.1)])
    assert src.numeric_year((0, 0, 1, 1), "latest") == 2023
    assert src.numeric_year((0, 0, 1, 1), "2020") == 2020
    monkeypatch.setattr(src, "available", lambda bbox, cache_dir=None: [])
    with pytest.raises(RuntimeError, match="no imagery"):
        src.numeric_year((0, 0, 1, 1), "latest")


def test_naip_fetch_accepts_latest(monkeypatch, tmp_path):
    from datetime import datetime
    from types import SimpleNamespace

    items = [SimpleNamespace(datetime=datetime(y, 7, 1), properties={"gsd": 0.6}) for y in (2018, 2022)]
    monkeypatch.setattr(hs, "naip_items", lambda bbox: items)
    seen = {}
    monkeypatch.setattr(hs, "fetch_naip", lambda bbox, year, out: seen.setdefault("year", year))
    hs.SOURCES["naip"].fetch((-100.0, 40.0, -99.99, 40.01), "latest", tmp_path / "x.tif")
    assert seen["year"] == 2022
