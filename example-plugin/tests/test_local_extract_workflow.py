import hashlib
import json

from base.datasources.local_osm import query_local_osm_roads
from base.datasources.query import LayerRequest, SpatialQuery
from base.plugins import list_plugins, run_plugin
from base.schemas import BoundingBox, GeoPoint
from bearing_matcher.plugin import RoadLayerSummary, match_bearings


def test_independent_bearing_plugin_consumes_local_query_result(tmp_path):
    bounds = BoundingBox(min_lat=56.0, min_lon=-3.0, max_lat=57.0, max_lon=-2.0)
    features = [{"type": "Feature", "id": f"osm-way-{i}",
        "geometry": {"type": "LineString", "coordinates": [[-2.8 + i*.01, 56.34], [-2.79 + i*.01, 56.35]]},
        "properties": {"highway": "residential"}} for i in range(3)]
    raw = json.dumps({"type": "FeatureCollection", "features": features}, sort_keys=True,
                     separators=(",", ":")).encode()
    path = tmp_path / "independent-consumer.geojson"
    path.write_bytes(raw)
    manifest = {"source_id": "fixture", "provider": "fixture OSM", "content_sha256": hashlib.sha256(raw).hexdigest(),
        "coverage": bounds.model_dump(), "coverage_complete": True, "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors"}
    (tmp_path / "independent-consumer.geojson.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    query = SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads")], offline=True)
    roads = query_local_osm_roads(query, path)
    observer_points = [GeoPoint(lat=feature["geometry"]["coordinates"][0][1],
                                lng=feature["geometry"]["coordinates"][0][0])
                       for feature in roads.data["features"]]
    ranked = match_bearings(observer_points, GeoPoint(lat=56.35, lng=-2.75), observed_bearing_deg=45)
    assert roads.coverage.status == "complete"
    assert "road_layer_summary" in [plugin.name for plugin in list_plugins()]
    summary = run_plugin("road_layer_summary", roads)
    assert isinstance(summary, RoadLayerSummary)
    assert summary.feature_count == 3
    assert summary.highway_counts == {"residential": 3}
    assert len(ranked) == 3
    assert all(candidate.confidence is None for candidate in ranked)
