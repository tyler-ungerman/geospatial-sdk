import json

import pytest

from base.datasources import IncompleteCoverageError, LayerRequest, SpatialQuery, query_osm_roads, osm
from base.datasources.http import HttpResponse, SourceUnavailable
from base.schemas import BoundingBox


def _query(bounds, *, require_complete=False, offline=False):
    return SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads")],
                        require_complete=require_complete, offline=offline)


def _feature(fid, lon=0.001):
    return {"type": "Feature", "id": f"way-{fid}",
            "geometry": {"type": "LineString", "coordinates": [[lon, .001], [lon, .002]]},
            "properties": {"highway": "residential"}}


def test_typed_query_does_not_silently_cap_merged_features(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.04)

    def tile(tile, kinds, **kwargs):
        offset = int(round(tile.min_lon / .02)) * 1001
        return {"type": "FeatureCollection", "features": [_feature(offset + i, tile.min_lon + .001) for i in range(1001)],
                "coverage_complete": True, "_completeness_verified": True}

    monkeypatch.setattr(osm, "_cached_tile", tile)
    result = query_osm_roads(_query(bounds, require_complete=True), limit=1500, cache_dir=tmp_path)
    assert len(result.data["features"]) == 2002
    assert result.coverage.status == "complete"
    assert result.diagnostics.feature_count == 2002
    assert result.provenance[0].source_id == "osm.roads"
    assert result.provenance[0].attribution == "© OpenStreetMap contributors"


def test_saturated_tile_is_partial_and_require_complete_fails(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)

    def tile(*args, **kwargs):
        return {"type": "FeatureCollection", "features": [_feature(1), _feature(2)],
                "coverage_complete": True, "_completeness_verified": True}

    monkeypatch.setattr(osm, "_cached_tile", tile)
    result = query_osm_roads(_query(bounds), limit=2, cache_dir=tmp_path)
    assert result.coverage.status == "partial"
    assert result.coverage.truncated
    assert result.data["coverage_complete"] is False
    assert result.data["data_status"] == "partial"
    with pytest.raises(IncompleteCoverageError) as exc:
        query_osm_roads(_query(bounds, require_complete=True), limit=2, cache_dir=tmp_path)
    assert exc.value.coverage.status == "partial"


def test_successful_empty_tile_is_complete(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    monkeypatch.setattr(osm, "_cached_tile", lambda *args, **kwargs: {
        "type": "FeatureCollection", "features": [], "coverage_complete": True,
        "_completeness_verified": True})
    result = query_osm_roads(_query(bounds, require_complete=True), cache_dir=tmp_path)
    assert result.coverage.status == "complete"
    assert result.data["features"] == []


def test_mixed_success_and_failed_tiles_report_unknown_tile(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=.001, min_lon=.001, max_lat=.01, max_lon=.04)

    def tile(tile, kinds, **kwargs):
        if tile.min_lon >= .02:
            raise SourceUnavailable("tile timeout")
        return {"type": "FeatureCollection", "features": [_feature(1)],
                "coverage_complete": True, "_completeness_verified": True}

    monkeypatch.setattr(osm, "_cached_tile", tile)
    result = query_osm_roads(_query(bounds), cache_dir=tmp_path)
    assert result.coverage.status == "partial"
    assert result.coverage.covered_tile_ids
    assert result.coverage.failed_tile_ids
    assert result.data["features"]
    assert result.coverage.verified_covered_geometry[0].min_lon == pytest.approx(.001)
    assert result.coverage.missing_or_unknown_geometry[0].min_lon == pytest.approx(.02)


@pytest.mark.parametrize("elements,remark", [
    ([{"type": "way", "id": 8, "tags": {"highway": "residential"}, "geometry": [{"lat": 1}]}], None),
    ([{"type": "relation", "id": 9, "tags": {"type": "multipolygon", "building": "yes"},
       "members": [{"type": "way", "role": "outer", "geometry": [{"lat": 0, "lon": 0}, {"lat": 1, "lon": 1}]}]}], None),
    ([], "runtime error: query timed out"),
])
def test_overpass_incomplete_responses_are_not_cached_or_complete(tmp_path, elements, remark):
    class Transport:
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            payload = {"elements": elements}
            if remark:
                payload["remark"] = remark
            return HttpResponse(200, json.dumps(payload).encode())

    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    result = osm.fetch_roads(bounds, ["residential"], client=Transport(), endpoint="https://overpass.test/api",
                             cache_dir=tmp_path)
    assert result["coverage_complete"] is False
    assert result["data_status"] == "partial"
    assert result["tile_records"][0]["status"] == "partial"
    assert result["errors"]
    assert not list(tmp_path.glob("*.json"))


def test_all_failed_empty_tiles_are_structured_failed_coverage(tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    query = SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads", filters={"highway_types": ["residential"]})], offline=True)
    result = query_osm_roads(query, cache_dir=tmp_path)
    assert result.coverage.status == "failed"
    assert result.coverage.failed_tile_ids
    assert result.data["features"] == []
    assert result.data["data_status"] == "failed"


def test_request_budget_caps_actual_transport_calls(monkeypatch, tmp_path):
    class Transport:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            return HttpResponse(200, b'{"elements":[]}')

    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.04)
    client = Transport()
    query = SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads")], max_requests=1)
    result = query_osm_roads(query, client=client, endpoint="https://overpass.test/api", cache_dir=tmp_path)
    assert client.calls == 1
    assert result.diagnostics.request_count == 1
    assert result.coverage.status == "partial"
    assert result.coverage.failed_tile_ids


def test_duplicate_way_on_tile_seam_is_returned_once(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.04)
    feature = _feature(7, .019)
    monkeypatch.setattr(osm, "_cached_tile", lambda *args, **kwargs: {
        "type": "FeatureCollection", "features": [feature], "coverage_complete": True,
        "_completeness_verified": True})
    result = query_osm_roads(_query(bounds), cache_dir=tmp_path)
    assert len(result.data["features"]) == 1
    assert result.data["features"][0]["id"] == "way-7"


def test_request_digest_includes_endpoint_and_limit(monkeypatch, tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    monkeypatch.setattr(osm, "_cached_tile", lambda *args, **kwargs: {
        "type": "FeatureCollection", "features": [], "coverage_complete": True,
        "_completeness_verified": True})
    a = query_osm_roads(_query(bounds), endpoint="https://a.test/api", limit=10, cache_dir=tmp_path)
    b = query_osm_roads(_query(bounds), endpoint="https://b.test/api", limit=10, cache_dir=tmp_path)
    c = query_osm_roads(_query(bounds), endpoint="https://a.test/api", limit=11, cache_dir=tmp_path)
    assert a.provenance[0].request_digest != b.provenance[0].request_digest
    assert a.provenance[0].request_digest != c.provenance[0].request_digest


def test_offline_query_replays_cached_normalized_data_without_network(tmp_path):
    class Transport:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            return HttpResponse(200, json.dumps({"elements": [{"type": "way", "id": 44,
                "tags": {"highway": "residential"}, "geometry": [
                    {"lat": .002, "lon": .002}, {"lat": .003, "lon": .003}]}]}).encode())

    bounds = BoundingBox(min_lat=.001, min_lon=.001, max_lat=.01, max_lon=.01)
    client = Transport()
    first = query_osm_roads(_query(bounds), client=client, endpoint="https://overpass.test/api", cache_dir=tmp_path)
    calls = client.calls
    second = query_osm_roads(_query(bounds, offline=True), client=client,
                             endpoint="https://overpass.test/api", cache_dir=tmp_path)
    assert client.calls == calls == 1
    assert first.data == second.data
    assert first.coverage.status == second.coverage.status == "complete"
    assert second.diagnostics.cache_hits == 1


def test_legacy_cache_is_readable_but_never_verified_complete(tmp_path):
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    old = {"type": "FeatureCollection", "features": [_feature(1)], "coverage_complete": True}
    (tmp_path / "v2_residential_0_0_2000.json").write_text(json.dumps(old), encoding="utf-8")
    query = SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads", filters={"highway_types": ["residential"]})], offline=True)
    result = query_osm_roads(query, cache_dir=tmp_path)
    assert len(result.data["features"]) == 1
    assert result.data["coverage_complete"] is False
    assert result.coverage.status == "partial"
    assert result.coverage.unknown_tile_ids == ["osm:0:0"]


def test_refresh_policy_bypasses_cache(tmp_path):
    class Transport:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            return HttpResponse(200, b'{"elements":[]}')

    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    client = Transport()
    query_osm_roads(_query(bounds), client=client, endpoint="https://refresh.test/api", cache_dir=tmp_path)
    query = SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads")], cache_policy="refresh")
    query_osm_roads(query, client=client, endpoint="https://refresh.test/api", cache_dir=tmp_path)
    assert client.calls == 2


def test_concurrent_query_diagnostics_are_isolated_and_legacy_metrics_accumulate(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=.01, max_lon=.01)
    osm.tile_cache_metrics(reset=True)

    class ImmediateTransport:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            self.calls += 1
            return HttpResponse(200, b'{"elements":[]}')

    prime_client = ImmediateTransport()
    endpoint_a = "https://cache-a.test/api"
    query_osm_roads(_query(bounds), client=prime_client, endpoint=endpoint_a, cache_dir=tmp_path)

    started, release = Event(), Event()

    class BlockingTransport:
        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            started.set()
            assert release.wait(timeout=5)
            return HttpResponse(200, b'{"elements":[]}')

    query_b = _query(bounds)
    query_a = _query(bounds, offline=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_b = pool.submit(query_osm_roads, query_b, client=BlockingTransport(),
                               endpoint="https://network-b.test/api", cache_dir=tmp_path)
        assert started.wait(timeout=5)
        future_a = pool.submit(query_osm_roads, query_a, endpoint=endpoint_a, cache_dir=tmp_path)
        result_a = future_a.result(timeout=5)
        release.set()
        result_b = future_b.result(timeout=5)

    assert result_a.diagnostics.cache_hits == 1
    assert result_a.diagnostics.request_count == 0
    assert result_a.provenance[0].cache_origin == "cache"
    assert result_b.diagnostics.cache_hits == 0
    assert result_b.diagnostics.request_count == 1
    assert result_b.provenance[0].cache_origin == "network"
    # Query execution no longer resets or steals the compatibility counters.
    totals = osm.tile_cache_metrics()
    assert totals["network_requests"] == 2
    assert totals["hits"] == 1
