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
