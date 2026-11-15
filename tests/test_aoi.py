import numpy as np
import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import box  # noqa: E402

from dgd.aoi import SNAP_M, grid_from_bbox, grid_from_point, grid_from_vector, utm_epsg  # noqa: E402


def test_utm_zones_both_hemispheres():
    assert utm_epsg(14.4, 41.1) == 32633
    assert utm_epsg(-1.27, 51.8) == 32630
    assert utm_epsg(-58.4, -34.6) == 32721


def test_grid_from_point_is_snapped_and_covers_the_point():
    g = grid_from_point(41.0963, 14.4009, 1500)
    assert g.crs == "EPSG:32633"
    assert g.res == 10
    assert g.transform.c % SNAP_M == 0 and g.transform.f % SNAP_M == 0
    assert 300 <= g.width <= 312 and 300 <= g.height <= 312
    min_lon, min_lat, max_lon, max_lat = g.lonlat_bbox()
    assert min_lon < 14.4009 < max_lon and min_lat < 41.0963 < max_lat
    lon, lat = g.center_lonlat()
    assert abs(lon - 14.4009) < 0.01 and abs(lat - 41.0963) < 0.01


def test_grid_key_is_stable_and_distinct():
    a = grid_from_point(41.0, 14.0, 500)
    assert a.key() == grid_from_point(41.0, 14.0, 500).key()
    assert a.key() != grid_from_point(41.0, 14.0, 600).key()


def test_blocks_tile_the_whole_grid():
    g = grid_from_point(41.0, 14.0, 700)
    seen = np.zeros(g.shape, dtype=int)
    for rs, cs in g.blocks(50):
        seen[rs, cs] += 1
    assert (seen == 1).all()


def test_bad_inputs_are_refused():
    with pytest.raises(ValueError, match="Latitude goes first"):
        grid_from_point(141.0, 14.0, 100)
    with pytest.raises(ValueError):
        grid_from_point(41.0, 14.0, 0)
    with pytest.raises(ValueError):
        grid_from_bbox(14.5, 41.0, 14.4, 41.1)


def test_grid_from_bbox_contains_bbox():
    g = grid_from_bbox(14.38, 41.08, 14.42, 41.11)
    min_lon, min_lat, max_lon, max_lat = g.lonlat_bbox()
    assert min_lon <= 14.38 and max_lon >= 14.42 and min_lat <= 41.08 and max_lat >= 41.11


def test_grid_from_vector_masks_outside(tmp_path):
    poly = box(14.39, 41.09, 14.40, 41.10)
    path = tmp_path / "aoi.geojson"
    gpd.GeoDataFrame({"name": ["x"]}, geometry=[poly], crs="EPSG:4326").to_file(path, driver="GeoJSON")
    grid, inside = grid_from_vector(str(path))
    assert inside.shape == grid.shape
    assert 0 < inside.mean() <= 1
    empty = tmp_path / "empty.geojson"
    empty.write_text('{"type":"FeatureCollection","features":[]}')
    with pytest.raises(ValueError):
        grid_from_vector(str(empty))
