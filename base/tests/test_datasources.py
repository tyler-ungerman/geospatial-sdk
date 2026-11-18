import json
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

import pytest

from base.datasources import fetch_all, fetch_source, get_source, list_sources, SourceUnavailable, FetchError
from base.datasources.osint import (
    fetch_earthquakes, fetch_firms, fetch_gdacs, fetch_opensky,
    fetch_osint_news, fetch_google_news, classify_event,
)
from base.datasources.http import HttpResponse
from base.datasources import osm, dem
from base.schemas import BoundingBox
from fixtures import FakeTransport, response

NOW = datetime(2026, 11, 17, 11, 0, tzinfo=timezone.utc)


def test_six_sources_loaded_and_reusable_without_R2():
    assert {s.name for s in list_sources()} >= {"usgs", "firms", "gdacs", "opensky", "gdelt", "google_news"}
    report = fetch_all(["usgs", "firms", "gdacs", "opensky", "gdelt", "google_news"],
                       client=FakeTransport(), options={"google_news": {"inter_feed_delay": 0}})
    assert not report.errors
    assert len(report.flatten("events")) == 7
    assert len(report.flatten("entities")) == 2
    assert len(report.records) == 6
    assert get_source("usgs").kind == "events"


def test_original_source_behaviors_and_heuristic_caveat():
    client = FakeTransport()
    quakes = fetch_earthquakes(client=client)
    assert len(quakes) == 2 and quakes[0]["severity"] == "high"
    assert quakes[0]["location_basis"] == "reported"
    assert "Tsunami warning" in quakes[0]["description"]
    fires = fetch_firms(client=client)
    assert len(fires) == 2 and fires[0]["raw_data"]["time"] == "0009"
    gdacs = fetch_gdacs(client=client, now=NOW)
    assert len(gdacs) == 1
    params = parse_qs(urlparse(client.urls[-1][1]).query)
    assert params["fromDate"] == ["2026-11-10"]
    assert params["toDate"] == ["2026-11-17"]
    aircraft = fetch_opensky(client=client, now=NOW)
    assert len(aircraft) == 2
    assert aircraft[0]["classification"] == "military"  # heuristic based on callsign
    assert aircraft[0]["altitude"] == 3281
    assert aircraft[0]["speed"] == 175
    assert aircraft[1]["classification"] == "unknown"
    with pytest.raises(ValueError):
        fetch_opensky(client=client, lamin=99)
    news = fetch_google_news(client=client, inter_feed_delay=0, ai_api_key="test")
    assert len(news) == 1 and news[0]["location_basis"] == "inferred"
    assert news[0]["location_uncertainty_m"] is None
    assert news[0]["lat"] == 0.0 and news[0]["country"] == "France"
    assert fetch_osint_news(client=client, ai_api_key="test")[0]["category"] == "military"
    assert classify_event("A fictional event", api_key="test", client=client)["summary"] == "A fictional event"


def test_failure_isolated_and_invalid_payload():
    client = FakeTransport(failure="opensky-network.org")
    report = fetch_all(["usgs", "opensky"], client=client)
    assert "opensky" in report.errors and len(report.records["usgs"]) == 2
    with pytest.raises(SourceUnavailable):
        fetch_opensky(client=client)
    with pytest.raises(KeyError):
        fetch_source("nonexistent", client=client)


class MapTransport:
    def request(self, method, url, *, headers=None, json_body=None, timeout=15):
        if "overpass" in url:
            assert "data=" in url
            return response({"elements": [{"type": "way", "id": 12, "tags": {"highway": "primary"},
                                             "geometry": [{"lat": 56.34, "lon": -2.80}, {"lat": 56.35, "lon": -2.79}]}]})
        if "opentopodata" in url:
            return response({"results": [{"elevation": e} for e in (10, 20, 30, 40)]})
        raise AssertionError(url)


def test_osm_and_dem_adapters_offline():
    bbox = BoundingBox(min_lat=56.3, min_lon=-2.9, max_lat=56.4, max_lon=-2.7)
    roads = osm.fetch_roads(bbox, client=MapTransport())
    assert roads["features"][0]["geometry"]["coordinates"] == [[-2.8, 56.34], [-2.79, 56.35]]
    grid = dem.fetch_patch(bbox, rows=2, cols=2, client=MapTransport())
    from base.schemas import GeoPoint
    assert grid.sample(GeoPoint(lat=56.35, lng=-2.8)) == pytest.approx(25)
    with pytest.raises(ValueError):
        dem.fetch_patch(bbox, rows=11, cols=10)
    with pytest.raises(ValueError):
        osm.fetch_roads(bbox, ["primary\";drop"])


def test_osm_tiles_cache_overlaps_and_negative_coordinates(tmp_path):
    class TileTransport:
        def __init__(self): self.calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            assert headers["User-Agent"] == osm.OSM_USER_AGENT
            return response({"elements": [{"type": "way", "id": 7,
                "tags": {"highway": "residential"},
                "nodes": [101, 102],
                "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.001, "lon": 0.001}]},
                {"type": "way", "id": 8, "tags": {"building": "yes"},
                 "geometry": [{"lat": 0.0, "lon": 0.0}, {"lat": 0.0, "lon": 0.001},
                              {"lat": 0.001, "lon": 0.001}, {"lat": 0.001, "lon": 0.0},
                              {"lat": 0.0, "lon": 0.0}]},
                {"type": "relation", "id": 9, "tags": {"type": "multipolygon", "natural": "water"},
                 "members": [{"type": "way", "role": "outer", "geometry": [
                    {"lat": -0.001, "lon": -0.001}, {"lat": -0.001, "lon": 0.001},
                    {"lat": 0.001, "lon": 0.001}, {"lat": 0.001, "lon": -0.001},
                    {"lat": -0.001, "lon": -0.001}]}]}]})

    osm.tile_cache_metrics(reset=True)
    client = TileTransport()
    first = BoundingBox(min_lat=-0.005, min_lon=-0.005, max_lat=0.005, max_lon=0.005)
    second = BoundingBox(min_lat=0.0, min_lon=0.0, max_lat=0.005, max_lon=0.005)
    a = osm.fetch_roads(first, ["residential"], client=client, cache_dir=tmp_path)
    assert len(a["features"]) == 3
    road = next(feature for feature in a["features"] if feature["properties"].get("highway"))
    building = next(feature for feature in a["features"] if feature["properties"].get("building"))
    assert road["properties"]["osm_nodes"] == [101, 102]
    assert building["geometry"]["type"] == "Polygon"
    water = next(feature for feature in a["features"] if feature["properties"].get("natural") == "water")
    assert water["id"] == "osm-relation-9"
    assert water["geometry"]["type"] == "Polygon"
    calls_after_first = client.calls
    b = osm.fetch_roads(second, ["residential"], client=client, cache_dir=tmp_path)
    assert len(b["features"]) == 3
    assert client.calls == calls_after_first
    metrics = osm.tile_cache_metrics()
    assert metrics["misses"] == calls_after_first
    assert metrics["hits"] >= 1
    assert metrics["network_requests"] == calls_after_first


def test_concurrent_osm_requests_share_the_same_tile_fetch(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import time

    class SlowTileTransport:
        def __init__(self): self.calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            time.sleep(.02)
            return response({"elements": [{"type": "way", "id": 12,
                "tags": {"highway": "residential"},
                "geometry": [{"lat": .002, "lon": .002}, {"lat": .003, "lon": .003}]}]})

    client = SlowTileTransport()
    bounds = BoundingBox(min_lat=.001, min_lon=.001, max_lat=.01, max_lon=.01)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: osm.fetch_roads(bounds, ["residential"],
            client=client, endpoint="https://overpass.test/api", cache_dir=tmp_path), range(4)))
    assert client.calls == 1
    assert all(len(result["features"]) == 1 for result in results)


def test_merged_osm_window_retains_features_from_every_tile(monkeypatch, tmp_path):
    """A per-tile response limit must not become a window-wide truncation."""
    def cached_tile(tile, kinds, **kwargs):
        lon = tile.min_lon + 0.001
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "id": f"way-{tile.min_lon}",
            "geometry": {"type": "LineString", "coordinates": [[lon, .001], [lon, .002]]},
            "properties": {"highway": "residential"}}]}

    monkeypatch.setattr(osm, "_cached_tile", cached_tile)
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.04)
    result = osm.fetch_roads(bounds, ["residential"], limit=1, cache_dir=tmp_path)
    assert len(result["features"]) == 2
    assert {feature["id"] for feature in result["features"]} == {"way-0.0", "way-0.02"}
    assert len(result["possibly_truncated_tiles"]) == 2


def test_offline_osm_mode_never_calls_network_on_cache_miss(monkeypatch, tmp_path):
    from base.datasources.http import SourceUnavailable

    class ForbiddenTransport:
        def request(self, *args, **kwargs):
            raise AssertionError("offline mode attempted a network request")

    monkeypatch.setenv("GEOSPATIAL_OSM_OFFLINE", "1")
    osm.tile_cache_metrics(reset=True)
    bounds = BoundingBox(min_lat=.001, min_lon=.001, max_lat=.01, max_lon=.01)
    with pytest.raises(SourceUnavailable, match="offline cache"):
        osm.fetch_roads(bounds, ["residential"], client=ForbiddenTransport(), cache_dir=tmp_path)
    metrics = osm.tile_cache_metrics()
    assert metrics["misses"] == 1
    assert metrics["network_requests"] == 0


def test_native_osm_oversized_tile_splits_without_losing_coverage():
    class SplitMapTransport:
        def __init__(self): self.calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            if self.calls == 1:
                return HttpResponse(400, b"You requested too many nodes")
            index = self.calls
            lat, lon = index/1000, index/1000
            xml = (f'<osm><node id="{index}" lat="{lat}" lon="{lon}"/>'
                   f'<node id="{index+100}" lat="{lat+0.0001}" lon="{lon+0.0001}"/>'
                   f'<way id="{index}"><nd ref="{index}"/><nd ref="{index+100}"/>'
                   '<tag k="highway" v="residential"/></way></osm>')
            return HttpResponse(200, xml.encode())

    client = SplitMapTransport()
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.02, max_lon=.02)
    result = osm._fetch_standard_map(bounds, ["residential"], client=client, limit=20)
    assert client.calls == 5
    assert len(result["features"]) == 4
    assert len({feature["id"] for feature in result["features"]}) == 4
    assert result["coverage_complete"] is True


def test_recursive_map_split_propagates_partial_child():
    class PartialSplitTransport:
        def __init__(self): self.calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            if self.calls == 1:
                return HttpResponse(400, b"too many nodes")
            if self.calls == 5:
                return HttpResponse(503, b"child unavailable")
            i = self.calls
            xml = (f'<osm><node id="{i}" lat="{i/1000}" lon="{i/1000}"/>'
                   f'<node id="{i+100}" lat="{i/1000+.0001}" lon="{i/1000+.0001}"/>'
                   f'<way id="{i}"><nd ref="{i}"/><nd ref="{i+100}"/>'
                   '<tag k="highway" v="residential"/></way></osm>')
            return HttpResponse(200, xml.encode())

    result = osm._fetch_standard_map(BoundingBox(min_lat=0, min_lon=0, max_lat=.02, max_lon=.02),
                                    ["residential"], client=PartialSplitTransport(), limit=20)
    assert len(result["features"]) == 3
    assert result["coverage_complete"] is False
    assert result["errors"]


def test_recursive_map_split_marks_parent_truncation_incomplete():
    class SaturatedSplitTransport:
        def __init__(self): self.calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            if self.calls == 1:
                return HttpResponse(400, b"too many nodes")
            ways = []
            for j in range(3):
                node = self.calls * 100 + j * 2
                ways.extend([
                    f'<node id="{node}" lat="{node/100000}" lon="{node/100000}"/>',
                    f'<node id="{node+1}" lat="{node/100000+.0001}" lon="{node/100000+.0001}"/>',
                    f'<way id="{node}"><nd ref="{node}"/><nd ref="{node+1}"/>'
                    '<tag k="highway" v="residential"/></way>'])
            return HttpResponse(200, ('<osm>' + ''.join(ways) + '</osm>').encode())

    result = osm._fetch_standard_map(BoundingBox(min_lat=0, min_lon=0, max_lat=.02, max_lon=.02),
                                    ["residential"], client=SaturatedSplitTransport(), limit=5)
    assert len(result["features"]) == 5
    assert result["coverage_complete"] is False
    assert any("feature limit" in error for error in result["errors"])


def test_partial_osm_tile_is_returned_but_never_cached(tmp_path):
    class PartialMapTransport:
        def __init__(self): self.map_calls = 0
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            if "overpass.test" in url:
                return HttpResponse(502, b"unavailable")
            self.map_calls += 1
            if self.map_calls == 1:
                return HttpResponse(400, b"too many nodes")
            if self.map_calls == 3:
                return HttpResponse(429, b"rate limited")
            i = self.map_calls
            xml = (f'<osm><node id="{i}" lat="{i/1000}" lon="{i/1000}"/>'
                   f'<node id="{i+100}" lat="{i/1000+.0001}" lon="{i/1000+.0001}"/>'
                   f'<way id="{i}"><nd ref="{i}"/><nd ref="{i+100}"/>'
                   '<tag k="highway" v="residential"/></way></osm>')
            return HttpResponse(200, xml.encode())

    client = PartialMapTransport()
    tile = BoundingBox(min_lat=0, min_lon=0, max_lat=.02, max_lon=.02)
    result = osm._cached_tile(tile, ["residential"], client=client, limit=20,
                              endpoints="https://overpass.test/api", cache_dir=tmp_path)
    assert len(result["features"]) == 3
    assert result["coverage_complete"] is False
    assert result["errors"]
    assert not list(tmp_path.glob("*.json"))
