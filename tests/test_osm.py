import numpy as np
import pytest

pyogrio = pytest.importorskip("pyogrio")

from dgd.highres import grid_from_bounds  # noqa: E402
from dgd.osm import built_mask, distance_to_ways, load_osm_file, overpass_urls  # noqa: E402

# A 100 m square near (52.0, 5.0) with one of everything the mask cares about.
LAT0, LON0 = 52.0, 5.0
DLAT, DLON = 1 / 111320, 1 / (111320 * np.cos(np.radians(LAT0)))


def _node(i, x, y):
    return f'<node id="{i}" version="1" lat="{LAT0 + y * DLAT:.8f}" lon="{LON0 + x * DLON:.8f}"/>'


def _square(first_id, x0, y0, x1, y1):
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    nodes = [_node(first_id + i, x, y) for i, (x, y) in enumerate(pts)]
    refs = [first_id + i for i in range(4)] + [first_id]
    return nodes, refs


def _way(wid, refs, tags):
    nd = "".join(f'<nd ref="{r}"/>' for r in refs)
    tg = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items())
    return f'<way id="{wid}" version="1">{nd}{tg}</way>'


@pytest.fixture
def osm_file(tmp_path):
    nodes, ways = [], []
    for wid, (box, tags) in enumerate([
        ((10, 10, 20, 20), {"building": "yes"}),
        ((30, 10, 50, 30), {"amenity": "parking"}),
        ((60, 60, 80, 80), {"highway": "pedestrian", "area": "yes"}),
        ((10, 60, 30, 80), {"natural": "water"}),
    ], start=1):
        n, refs = _square(100 * wid, *box)
        nodes += n
        ways.append(_way(wid, refs, tags))
    nodes += [_node(900, 0, 50), _node(901, 100, 50), _node(910, 0, 95), _node(911, 100, 95)]
    ways.append(_way(9, [900, 901], {"highway": "residential"}))
    ways.append(_way(10, [910, 911], {"waterway": "ditch"}))  # a line that is not a road
    xml = '<?xml version="1.0" encoding="UTF-8"?><osm version="0.6" generator="test">' + "".join(nodes) + "".join(ways) + "</osm>"
    path = tmp_path / "tiny.osm"
    path.write_text(xml)
    return path


def _grid():
    from pyproj import Transformer

    crs = "EPSG:32631"
    x0, y0 = Transformer.from_crs("EPSG:4326", crs, always_xy=True).transform(LON0, LAT0)
    return grid_from_bounds(crs, x0, y0, x0 + 100, y0 + 100, 1.0)


def test_local_extract_is_read_and_masked(osm_file):
    bbox = (LON0 - 0.001, LAT0 - 0.001, LON0 + 0.003, LAT0 + 0.003)
    osm = load_osm_file(str(osm_file), bbox)
    assert len(osm["buildings"]) == 1 and len(osm["water"]) == 1 and len(osm["paved"]) == 2
    assert [k for k, _ in osm["ways"]] == ["residential"]
    grid = _grid()
    mask = built_mask(grid, osm)
    # Row index counts down from the top: y metres up is row (99 - y).
    assert mask[99 - 15, 15] and mask[99 - 20, 40] and mask[99 - 70, 70] and mask[99 - 70, 20]
    assert mask[99 - 50, 90]  # the residential road surface
    assert not mask[99 - 90, 50]  # open ground stays searchable
    dist = distance_to_ways(grid, [c for _, c in osm["ways"]])
    assert dist[99 - 50, 50] < 1.5 and 35 < dist[99 - 90, 50] < 45


def test_own_overpass_server_goes_first(monkeypatch):
    monkeypatch.setenv("DGD_OVERPASS_URL", "http://overpass.police.local/api/interpreter")
    urls = overpass_urls()
    assert urls[0] == "http://overpass.police.local/api/interpreter" and len(urls) > 1
    monkeypatch.delenv("DGD_OVERPASS_URL")
    assert overpass_urls()[0].startswith("https://overpass-api.de")


def test_incomplete_overpass_answers_are_retried_and_never_cached(tmp_path, monkeypatch):
    import io
    import json

    from dgd import osm

    timed_out = {"remark": 'runtime error: Query timed out in "query" at line 1 after 91 seconds.', "elements": []}
    answers = [timed_out, {"elements": []}]
    monkeypatch.delenv("DGD_OVERPASS_URL", raising=False)
    monkeypatch.setattr(osm, "OVERPASS_URLS", ["http://a", "http://b"])
    monkeypatch.setattr(osm.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(answers.pop(0)).encode()))
    assert osm.fetch_osm((5.0, 52.0, 5.01, 52.01), cache_dir=tmp_path)["ways"] == []
    assert not answers  # the second server was asked
    cached = list((tmp_path / "osm").glob("*.json"))
    assert len(cached) == 1 and "remark" not in json.loads(cached[0].read_text())

    # An incomplete answer cached by an older version is asked again, and
    # when every server times out that is an error, not an empty map.
    cached[0].write_text(json.dumps(timed_out))
    answers[:] = [timed_out, timed_out]
    with pytest.raises(RuntimeError, match="timed out"):
        osm.fetch_osm((5.0, 52.0, 5.01, 52.01), cache_dir=tmp_path)
