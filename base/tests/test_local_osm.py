import hashlib
import json
import zipfile

import pytest

from base.datasources.local_osm import query_local_osm_roads, snapshot_local_osm_extract
from base.datasources.query import IncompleteCoverageError, LayerRequest, SpatialQuery
from base.schemas import BoundingBox


def _feature(fid="way-1", coordinates=None):
    return {"type": "Feature", "id": fid, "geometry": {"type": "LineString",
            "coordinates": coordinates or [[-1, 50], [-.99, 50.01]]},
            "properties": {"highway": "residential"}}


def _write_extract(directory, *, features=None, coverage=None, complete=True):
    data_path = directory / "roads.geojson"
    raw = json.dumps({"type": "FeatureCollection", "features": features or [_feature()]},
                     sort_keys=True, separators=(",", ":")).encode()
    data_path.write_bytes(raw)
    manifest = {"source_id": "regional-osm-test", "provider": "local OSM extract",
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "coverage": (coverage or BoundingBox(min_lat=49, min_lon=-2, max_lat=51, max_lon=0)).model_dump(),
        "coverage_complete": complete, "licence": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors", "retrieved_at": "2026-11-17T12:00:00Z"}
    (directory / "roads.geojson.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return data_path


def _query(bounds, *, require_complete=False):
    return SpatialQuery(region=bounds, layers=[LayerRequest(name="osm.roads")],
                        offline=True, require_complete=require_complete)


def test_manifested_extract_returns_complete_coverage_and_provenance(tmp_path):
    path = _write_extract(tmp_path)
    bounds = BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5)
    result = query_local_osm_roads(_query(bounds, require_complete=True), path)
    assert result.coverage.status == "complete"
    assert result.coverage.verified_covered_geometry == [bounds]
    assert result.coverage.missing_or_unknown_geometry == []
    assert result.provenance[0].source_id == "regional-osm-test"
    assert result.provenance[0].content_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.provenance[0].licence == "ODbL-1.0"
    assert result.provenance[0].retrieved_at.isoformat().startswith("2026-11-17T12:00:00")


def test_missing_manifest_and_uncovered_extent_never_claim_complete(tmp_path):
    path = tmp_path / "bare.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [_feature()]}), encoding="utf-8")
    bounds = BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5)
    unknown = query_local_osm_roads(_query(bounds), path)
    assert unknown.coverage.status == "unknown"
    assert unknown.provenance[0].retrieved_at is None
    assert unknown.provenance[0].licence is None
    with pytest.raises(IncompleteCoverageError):
        query_local_osm_roads(_query(bounds, require_complete=True), path)

    path = _write_extract(tmp_path, coverage=BoundingBox(min_lat=49, min_lon=-2, max_lat=50, max_lon=-1))
    partial = query_local_osm_roads(_query(bounds), path)
    assert partial.coverage.status == "partial"
    assert partial.coverage.verified_covered_geometry
    assert partial.coverage.missing_or_unknown_geometry


def test_extract_checksum_mismatch_is_rejected(tmp_path):
    path = _write_extract(tmp_path)
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="checksum"):
        query_local_osm_roads(_query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5)), path)


@pytest.mark.parametrize("crs", [
    {"type": "name", "properties": {"name": "EPSG:3857"}},
    {"type": "name", "properties": {"name": "EPSG:4326"}},
    {"type": "link", "properties": {"href": "https://example.test/crs"}},
])
def test_unsupported_geojson_crs_is_rejected_before_completeness_claim(tmp_path, crs):
    path = _write_extract(tmp_path)
    collection = json.loads(path.read_bytes())
    collection["crs"] = crs
    raw = json.dumps(collection, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    manifest_path = tmp_path / "roads.geojson.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="CRS"):
        query_local_osm_roads(_query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5),
                                     require_complete=True), path)


def test_explicit_crs84_is_accepted(tmp_path):
    path = _write_extract(tmp_path)
    collection = json.loads(path.read_bytes())
    collection["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    raw = json.dumps(collection, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    manifest_path = tmp_path / "roads.geojson.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = query_local_osm_roads(_query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5),
                                          require_complete=True), path)
    assert result.coverage.status == "complete"


@pytest.mark.parametrize("crs", [
    {"type": "name", "properties": {"name": "EPSG:3857"}},
    {"type": "name", "properties": {"name": "EPSG:4326"}},
    {"type": "link", "properties": {"href": "https://example.test/crs"}},
])
def test_unsupported_geojson_crs_is_rejected_before_completeness_claim(tmp_path, crs):
    path = _write_extract(tmp_path)
    collection = json.loads(path.read_bytes())
    collection["crs"] = crs
    raw = json.dumps(collection, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    manifest_path = tmp_path / "roads.geojson.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="CRS"):
        query_local_osm_roads(_query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5),
                                     require_complete=True), path)


def test_explicit_crs84_is_accepted(tmp_path):
    path = _write_extract(tmp_path)
    collection = json.loads(path.read_bytes())
    collection["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    raw = json.dumps(collection, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    manifest_path = tmp_path / "roads.geojson.manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["content_sha256"] = hashlib.sha256(raw).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = query_local_osm_roads(_query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5),
                                          require_complete=True), path)
    assert result.coverage.status == "complete"


def test_duplicate_osm_id_conflict_is_reported_without_dropping_in_bounds_feature(tmp_path):
    bounds = BoundingBox(min_lat=.5, min_lon=.5, max_lat=.6, max_lon=.6)
    outside = _feature("way-duplicate", [[0, 0], [.1, .1]])
    inside = _feature("way-duplicate", [[.55, .55], [.56, .56]])
    path = _write_extract(tmp_path, features=[outside, inside], coverage=bounds)
    result = query_local_osm_roads(_query(bounds), path)
    assert result.coverage.status == "partial"
    assert result.coverage.missing_or_unknown_geometry == [bounds]
    assert len(result.data["features"]) == 1
    assert "#conflict-" in result.data["features"][0]["id"]
    assert any("conflicting duplicate" in error for error in result.diagnostics.errors)


def test_polyline_envelope_overlap_without_segment_intersection_is_excluded(tmp_path):
    bounds = BoundingBox(min_lat=.5, min_lon=.5, max_lat=.6, max_lon=.6)
    disjoint = _feature("way-envelope-only", [[0, 1], [1, 1], [1, 0]])
    path = _write_extract(tmp_path, features=[disjoint], coverage=bounds)
    result = query_local_osm_roads(_query(bounds, require_complete=True), path)
    assert result.coverage.status == "complete"
    assert result.data["features"] == []
    assert result.data["data_status"] == "complete_empty"


def test_unsupported_road_geometry_marks_entire_requested_area_unknown(tmp_path):
    bounds = BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5)
    polygon_road = {"type": "Feature", "id": "area-road", "geometry": {"type": "Polygon",
        "coordinates": [[[-1, 50], [-.99, 50], [-.99, 50.01], [-1, 50.01], [-1, 50]]]},
        "properties": {"highway": "residential"}}
    path = _write_extract(tmp_path, features=[polygon_road])
    result = query_local_osm_roads(_query(bounds), path)
    assert result.coverage.status == "partial"
    assert result.coverage.verified_covered_geometry == []
    assert result.coverage.missing_or_unknown_geometry == [bounds]
    assert result.data["features"] == []


def test_snapshot_rejects_oversized_uncompressed_member(tmp_path, monkeypatch):
    from base.datasources import local_osm

    monkeypatch.setattr(local_osm, "MAX_EXTRACT_BYTES", 128)
    path = tmp_path / "oversized.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("data.geojson", b"x" * 129)
        archive.writestr("manifest.json", b"{}")
    with pytest.raises(ValueError, match="maximum uncompressed size"):
        query_local_osm_roads(_query(BoundingBox(min_lat=0, min_lon=0, max_lat=1, max_lon=1)), path)


def test_deterministic_snapshot_replay_is_relocatable_and_checksum_checked(tmp_path):
    path = _write_extract(tmp_path)
    first = snapshot_local_osm_extract(path, tmp_path / "first.zip")
    second = snapshot_local_osm_extract(path, tmp_path / "second.zip")
    assert first.read_bytes() == second.read_bytes()
    query = _query(BoundingBox(min_lat=49.5, min_lon=-1.5, max_lat=50.5, max_lon=-.5), require_complete=True)
    replayed = query_local_osm_roads(query, first)
    assert replayed.coverage.status == "complete"
    assert replayed.data["features"] == query_local_osm_roads(query, second).data["features"]

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(first) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members["data.geojson"] += b" "
    with zipfile.ZipFile(tampered, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    with pytest.raises(ValueError, match="checksum"):
        query_local_osm_roads(query, tampered)
