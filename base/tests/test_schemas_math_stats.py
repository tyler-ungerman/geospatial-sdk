import json
import math
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from base.schemas import BoundingBox, EvidenceItem, GeoCandidate, GeoPoint, GeoEvent, ElevationGrid
from base.geo_math import (haversine_m, initial_bearing_deg, destination_point,
                           bbox_from_radius, web_mercator, from_web_mercator,
                           line_of_sight, horizon_profile)
from base.stats import (cluster_points, aggregate_evidence, posterior_probability,
                        geodesic_errors_m, recall_at_k, coverage_at_radius, expected_calibration_error)
from base.modeling.metrics import ndmi, ndvi
from base.modeling.statistics import percentile
from base.reporting import feature_collection, export_geojson, export_html


def test_strict_schemas_and_serialization():
    with pytest.raises(ValidationError):
        GeoPoint(lat=91, lng=0)
    with pytest.raises(ValidationError):
        GeoPoint(lat=0, lng=181)
    with pytest.raises(ValidationError):
        GeoPoint(lat=0, lng=0, uncertainty_m=-1)
    with pytest.raises(ValidationError):
        GeoPoint(lat=0, lng=0, foo="bar")
    with pytest.raises(ValidationError):
        BoundingBox(min_lat=5, max_lat=4, min_lon=0, max_lon=1)
    point = GeoPoint(lat=56.34, lng=-2.79, uncertainty_m=100, basis="derived")
    evidence = EvidenceItem(id="e", source="lidar", description="cone", score=.5,
                            recorded_at=datetime.now(timezone.utc))
    candidate = GeoCandidate(id="c", location=point, evidence=[evidence])
    assert json.loads(candidate.model_dump_json())["evidence"][0]["recorded_at"]
    assert point.coordinates() == [-2.79, 56.34]
    with pytest.raises(ValidationError):
        GeoCandidate(id="none", location=point, evidence=[])


def test_geospatial_distance_bearings_destination_projection():
    a, b = GeoPoint(lat=0, lng=0), GeoPoint(lat=0, lng=1)
    assert haversine_m(a, b) == pytest.approx(111195, rel=1e-4)
    assert initial_bearing_deg(a, b) == pytest.approx(90)
    result = destination_point(a, 90, haversine_m(a, b))
    assert result.lat == pytest.approx(0, abs=1e-6)
    assert result.lng == pytest.approx(1, abs=1e-6)
    bounds = bbox_from_radius(GeoPoint(lat=56.34, lng=-2.8), 1000)
    assert bounds.contains(GeoPoint(lat=56.34, lng=-2.8))
    p = GeoPoint(lat=56.34, lng=-2.8)
    assert from_web_mercator(*web_mercator(p)).lat == pytest.approx(p.lat)
    with pytest.raises(ValueError):
        web_mercator(GeoPoint(lat=89, lng=0))
    with pytest.raises(ValueError):
        bbox_from_radius(GeoPoint(lat=0, lng=179.999), 10_000)


def test_elevation_and_visibility():
    bounds = BoundingBox(min_lat=0, min_lon=0, max_lat=1, max_lon=1)
    grid = ElevationGrid(bounds=bounds, elevations_m=[[0, 10], [20, 30]])
    assert grid.sample(GeoPoint(lat=.5, lng=.5)) == 15
    a, b = GeoPoint(lat=0, lng=0), GeoPoint(lat=0, lng=.001)
    assert line_of_sight(a, b, lambda _: 0, earth_curvature=False) is True
    assert line_of_sight(a, b, lambda p: 30 if .0003 < p.lng < .0007 else 0,
                         earth_curvature=False) is False
    assert line_of_sight(a, b, lambda _: None) is None
    assert len(horizon_profile(a, [[b]], lambda _: 0)) == 1


def test_clustering_ranking_and_non_probabilities():
    points = [GeoPoint(lat=0, lng=0), GeoPoint(lat=0, lng=.001), GeoPoint(lat=10, lng=10)]
    assert cluster_points(points, eps_m=150, min_samples=2) == [0, 0, -1]
    e1 = EvidenceItem(id="a", source="obs", description="matches", score=1, reliability=.9)
    e2 = EvidenceItem(id="b", source="obs", description="contradicts", score=-1, reliability=.8)
    c1 = GeoCandidate(id="1", location=points[0], evidence=[e1])
    c2 = GeoCandidate(id="2", location=points[1], evidence=[e2])
    ranked = aggregate_evidence([c2, c1])
    assert [c.id for c in ranked] == ["1", "2"]
    assert ranked[0].rank_score == 1 and ranked[0].confidence is None
    assert posterior_probability(.5, [3, 2]) == pytest.approx(6/7)
    with pytest.raises(ValueError):
        posterior_probability(.5, [-1])


def test_metrics_eval_and_reports(tmp_path):
    point = GeoPoint(lat=5, lng=6, uncertainty_m=50)
    actual = GeoPoint(lat=5, lng=6)
    assert geodesic_errors_m([point], [actual]) == [0]
    assert recall_at_k([["a", "b"], ["c"]], ["b", "c"], 2) == 1
    assert coverage_at_radius([point], [actual]) == 1
    assert expected_calibration_error([1, 0], [True, False]) == 0
    assert ndvi(.8, .2) == pytest.approx(.6)
    assert ndmi(.8, .2) == pytest.approx(.6)
    assert ndmi(0, 0) is None
    assert percentile([1, 2, 3, 4], 50) == 2.5
    event = GeoEvent(id="e", source="test", title="Sample", category="disaster",
                     severity="low", location=point)
    geojson = feature_collection([event])
    assert geojson["features"][0]["geometry"]["coordinates"] == [6, 5]
    path = export_geojson([event], tmp_path / "map.geojson")
    assert json.loads(path.read_text())["features"][0]["properties"]["uncertainty_m"] == 50
    html = export_html([event], tmp_path / "map.html")
    assert html.exists() and "Uncertainty" in html.read_text()


def test_solar_position_and_crs_conversion():
    from base.geo_math import sun_position, transform_xy
    at_equinox = datetime(2026, 3, 20, 12, tzinfo=timezone.utc)
    azimuth, elevation = sun_position(GeoPoint(lat=51.5, lng=0), at_equinox)
    assert 170 < azimuth < 190
    assert 37 < elevation < 41  # equinox noon altitude at Greenwich ≈38.5°
    x, y = transform_xy(-2.8, 56.34, from_crs="EPSG:4326", to_crs="EPSG:3857")
    lon, lat = transform_xy(x, y, from_crs="EPSG:3857", to_crs="EPSG:4326")
    assert lon == pytest.approx(-2.8)
    assert lat == pytest.approx(56.34)
