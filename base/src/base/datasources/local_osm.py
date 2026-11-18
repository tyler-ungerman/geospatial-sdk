"""Offline OSM GeoJSON extracts with explicit coverage manifests."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import io
import json
import math
from pathlib import Path
from typing import Any
import zipfile

from pydantic import Field, field_validator

from base.schemas import BoundingBox, Contract
from .query import (CoverageReport, IncompleteCoverageError, LayerResult,
                    QueryDiagnostics, SourceRecord, SpatialQuery)

MAX_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ExtractManifest(Contract):
    """User-supplied source claims, tied to the exact GeoJSON bytes by SHA-256."""

    source_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    coverage: BoundingBox | None = None
    coverage_complete: bool | None = None
    retrieved_at: datetime | None = None
    source_version: str | None = None
    licence: str | None = None
    attribution: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def retrieval_time_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("retrieved_at must include a timezone")
        return value


def _sidecar_path(data_path: Path) -> Path:
    return data_path.with_suffix(data_path.suffix + ".manifest.json")


def _validate_geojson_crs(collection: dict) -> None:
    """Accept only GeoJSON's default lon/lat axes or explicit OGC CRS84."""
    if "crs" not in collection:
        return
    crs = collection["crs"]
    if not isinstance(crs, dict) or crs.get("type") != "name":
        raise ValueError("local OSM GeoJSON CRS must explicitly use a supported named CRS84")
    properties = crs.get("properties")
    name = properties.get("name") if isinstance(properties, dict) else None
    supported = {
        "CRS84",
        "OGC:CRS84",
        "urn:ogc:def:crs:OGC:1.3:CRS84",
        "http://www.opengis.net/def/crs/OGC/1.3/CRS84",
    }
    if name not in supported:
        raise ValueError(f"unsupported GeoJSON CRS {name!r}; only CRS84 longitude/latitude axes are supported")


def _read_source(path: Path, manifest_path: str | Path | None) -> tuple[bytes, ExtractManifest | None]:
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if sorted(names) != ["data.geojson", "manifest.json"] or len(names) != 2:
                raise ValueError("snapshot must contain exactly data.geojson and manifest.json")
            members = {info.filename: info for info in archive.infolist()}
            if members["data.geojson"].file_size > MAX_EXTRACT_BYTES:
                raise ValueError("snapshot GeoJSON exceeds the maximum uncompressed size")
            if members["manifest.json"].file_size > MAX_MANIFEST_BYTES:
                raise ValueError("snapshot manifest exceeds the maximum size")
            with archive.open("data.geojson") as stream:
                data_bytes = stream.read(MAX_EXTRACT_BYTES + 1)
            with archive.open("manifest.json") as stream:
                manifest_bytes = stream.read(MAX_MANIFEST_BYTES + 1)
            if len(data_bytes) > MAX_EXTRACT_BYTES or len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise ValueError("snapshot member exceeds configured size limit")
    else:
        if path.stat().st_size > MAX_EXTRACT_BYTES:
            raise ValueError("local OSM GeoJSON exceeds the maximum size")
        data_bytes = path.read_bytes()
        sidecar = Path(manifest_path) if manifest_path is not None else _sidecar_path(path)
        if not sidecar.exists():
            return data_bytes, None
        if sidecar.stat().st_size > MAX_MANIFEST_BYTES:
            raise ValueError("local extract manifest exceeds the maximum size")
        manifest_bytes = sidecar.read_bytes()
    try:
        manifest_raw = json.loads(manifest_bytes)
        manifest = ExtractManifest.model_validate(manifest_raw)
    except Exception as exc:
        raise ValueError(f"invalid local extract manifest: {exc}") from exc
    digest = sha256(data_bytes).hexdigest()
    if digest != manifest.content_sha256:
        raise ValueError("local extract checksum does not match its manifest")
    return data_bytes, manifest


def _position_pairs(value: Any):
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and isinstance(value[0], (int, float)) and isinstance(value[1], (int, float)):
            yield value[0], value[1]
        else:
            for child in value:
                yield from _position_pairs(child)


def _segment_intersects_bounds(a: tuple[float, float], b: tuple[float, float], bounds: BoundingBox) -> bool:
    """Liang-Barsky segment/rectangle intersection in GeoJSON lon/lat axes."""
    x0, y0 = a
    dx, dy = b[0] - x0, b[1] - y0
    lower, upper = 0.0, 1.0
    for p, q in ((-dx, x0 - bounds.min_lon), (dx, bounds.max_lon - x0),
                 (-dy, y0 - bounds.min_lat), (dy, bounds.max_lat - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def _feature_intersects(feature: dict, bounds: BoundingBox) -> bool:
    geometry = feature["geometry"]
    lines = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    return any(_segment_intersects_bounds(tuple(a[:2]), tuple(b[:2]), bounds)
               for line in lines for a, b in zip(line, line[1:]))


def _valid_geometry(geometry: Any) -> bool:
    if not isinstance(geometry, dict):
        return False
    kind = geometry.get("type")
    if kind not in {"LineString", "MultiLineString"}:
        return False
    coordinates = geometry.get("coordinates")

    def position(value: Any) -> bool:
        return (isinstance(value, (list, tuple)) and len(value) >= 2 and
                all(isinstance(number, (int, float)) and math.isfinite(number) for number in value[:2]) and
                -180 <= value[0] <= 180 and -90 <= value[1] <= 90)

    def line(value: Any) -> bool:
        return isinstance(value, list) and len(value) >= 2 and all(position(item) for item in value)

    def ring(value: Any) -> bool:
        return line(value) and len(value) >= 4 and value[0][:2] == value[-1][:2]

    if kind == "LineString":
        return line(coordinates)
    return isinstance(coordinates, list) and bool(coordinates) and all(line(item) for item in coordinates)


def _intersection(a: BoundingBox, b: BoundingBox) -> BoundingBox | None:
    values = (max(a.min_lat, b.min_lat), max(a.min_lon, b.min_lon),
              min(a.max_lat, b.max_lat), min(a.max_lon, b.max_lon))
    if values[0] >= values[2] or values[1] >= values[3]:
        return None
    return BoundingBox(min_lat=values[0], min_lon=values[1], max_lat=values[2], max_lon=values[3])


def _difference(requested: BoundingBox, covered: BoundingBox | None) -> list[BoundingBox]:
    if covered is None:
        return [requested]
    boxes = []
    candidates = [
        (requested.min_lat, requested.min_lon, covered.min_lat, requested.max_lon),
        (covered.max_lat, requested.min_lon, requested.max_lat, requested.max_lon),
        (covered.min_lat, requested.min_lon, covered.max_lat, covered.min_lon),
        (covered.min_lat, covered.max_lon, covered.max_lat, requested.max_lon),
    ]
    for min_lat, min_lon, max_lat, max_lon in candidates:
        if min_lat < max_lat and min_lon < max_lon:
            boxes.append(BoundingBox(min_lat=min_lat, min_lon=min_lon, max_lat=max_lat, max_lon=max_lon))
    return boxes


def query_local_osm_roads(query: SpatialQuery, path: str | Path, *,
                           manifest_path: str | Path | None = None) -> LayerResult:
    """Query a local GeoJSON extract or snapshot. This function never uses network."""
    roads_request = next((layer for layer in query.layers if layer.name == "osm.roads"), None)
    if roads_request is None:
        raise ValueError("query_local_osm_roads requires a layer named 'osm.roads'")
    if any(layer.name != "osm.roads" for layer in query.layers):
        raise ValueError("query_local_osm_roads only supports the 'osm.roads' layer")
    if query.cache_policy == "refresh":
        raise ValueError("local OSM extracts have no network cache to refresh")
    source_path = Path(path)
    raw, manifest = _read_source(source_path, manifest_path)
    try:
        collection = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("local OSM extract is not valid JSON") from exc
    if not isinstance(collection, dict) or collection.get("type") != "FeatureCollection" or not isinstance(collection.get("features"), list):
        raise ValueError("local OSM extract must be a GeoJSON FeatureCollection")
    _validate_geojson_crs(collection)

    requested_highways = roads_request.filters.get("highway_types")
    if requested_highways is not None and (not isinstance(requested_highways, list) or
            any(not isinstance(value, str) for value in requested_highways)):
        raise ValueError("highway_types must be a list of strings")
    requested_set = set(requested_highways) if requested_highways is not None else None
    features, parse_errors, seen = [], [], {}
    for index, feature in enumerate(collection["features"]):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            parse_errors.append(f"feature {index} is not a GeoJSON Feature")
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            parse_errors.append(f"feature {index} has invalid properties")
            continue
        highway = props.get("highway")
        if highway is None:
            continue
        if not isinstance(highway, str):
            parse_errors.append(f"relevant road feature {feature.get('id', index)} has invalid highway tag")
            continue
        if requested_set is not None and highway not in requested_set:
            continue
        geometry = feature.get("geometry")
        if not _valid_geometry(geometry) or not list(_position_pairs(geometry.get("coordinates"))):
            parse_errors.append(f"relevant road feature {feature.get('id', index)} has invalid geometry")
            continue
        raw_id = feature.get("id")
        if raw_id is not None and not isinstance(raw_id, (str, int)):
            parse_errors.append(f"relevant road feature {index} has an invalid ID")
            continue
        feature_id = str(raw_id if raw_id is not None else sha256(json.dumps(feature, sort_keys=True).encode()).hexdigest())
        canonical_feature = json.dumps(feature, sort_keys=True, separators=(",", ":"))
        prior_versions = seen.setdefault(feature_id, set())
        if canonical_feature in prior_versions:
            continue
        if prior_versions:
            parse_errors.append(f"conflicting duplicate OSM feature ID {feature_id}")
            feature_id = f"{feature_id}#conflict-{sha256(canonical_feature.encode()).hexdigest()[:12]}"
        prior_versions.add(canonical_feature)
        normalized = dict(feature)
        normalized["id"] = feature_id
        if _feature_intersects(normalized, query.region):
            features.append(normalized)
    features.sort(key=lambda item: str(item["id"]))

    verified_coverage = None
    covered_geometry: list[BoundingBox] = []
    missing = [query.region]
    status = "unknown"
    reasons = ["no checksum-verified source manifest; extent and completeness are unknown"]
    if manifest is not None:
        verified_coverage = _intersection(query.region, manifest.coverage) if manifest.coverage else None
        if manifest.coverage_complete is True and manifest.coverage is not None:
            missing = _difference(query.region, verified_coverage)
            if not parse_errors and verified_coverage is not None:
                covered_geometry = [verified_coverage]
            else:
                covered_geometry = []
            if not missing and not parse_errors:
                status = "complete"
                reasons = []
            else:
                status = "partial"
                reasons = ["declared extract extent does not cover the full query" if missing else "extract parsing was incomplete"]
        else:
            status = "partial" if manifest.coverage is not None and verified_coverage is not None else "unknown"
            reasons = ["manifest does not assert complete feature enumeration"]
            if parse_errors:
                reasons.extend(parse_errors)
        if parse_errors and status == "complete":
            status = "partial"
            missing = [query.region]
            reasons = ["extract parsing was incomplete", *parse_errors]
        elif parse_errors:
            missing = [query.region]
            covered_geometry = []
            reasons.extend(parse_errors)

    if manifest is None and parse_errors:
        reasons.extend(parse_errors)
    coverage = CoverageReport(requested_geometry=query.region,
        verified_covered_geometry=covered_geometry if manifest is not None and manifest.coverage_complete else [],
        missing_or_unknown_geometry=missing if status != "complete" else [],
        status=status, reasons=reasons)
    if query.require_complete and status != "complete":
        raise IncompleteCoverageError(coverage)

    source_digest = sha256(raw).hexdigest()
    identity = {"query": query.model_dump(exclude_none=True), "source_id": manifest.source_id if manifest else source_path.name,
                "content_sha256": source_digest, "adapter": "local-osm-geojson.v1"}
    provenance = [SourceRecord(provider=manifest.provider if manifest else "local-geojson",
        source_id=manifest.source_id if manifest else source_path.name,
        retrieved_at=manifest.retrieved_at if manifest else None,
        source_version=manifest.source_version if manifest else None,
        request_digest=sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        content_sha256=source_digest, licence=manifest.licence if manifest else None,
        attribution=manifest.attribution if manifest else None, cache_origin="unknown")]
    return LayerResult(data={"type": "FeatureCollection", "features": features,
                             "data_status": "complete_empty" if status == "complete" and not features else status},
        coverage=coverage, provenance=provenance,
        diagnostics=QueryDiagnostics(feature_count=len(features), errors=parse_errors))


def snapshot_local_osm_extract(path: str | Path, output_path: str | Path, *,
                               manifest_path: str | Path | None = None) -> Path:
    """Create a deterministic relocatable ZIP after verifying source checksum metadata."""
    source = Path(path)
    if source.suffix.lower() == ".zip":
        raise ValueError("snapshot source must be a GeoJSON file, not an existing snapshot")
    raw, manifest = _read_source(source, manifest_path)
    if manifest is None:
        raise ValueError("snapshot requires a checksum and provenance manifest")
    manifest_bytes = json.dumps(manifest.model_dump(mode="json", exclude_none=True),
                                sort_keys=True, separators=(",", ":")).encode() + b"\n"
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    snapshot_bytes = io.BytesIO()
    with zipfile.ZipFile(snapshot_bytes, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in (("data.geojson", raw), ("manifest.json", manifest_bytes)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            archive.writestr(info, content)
    temporary = destination.with_name(destination.name + ".tmp")
    temporary.write_bytes(snapshot_bytes.getvalue())
    temporary.replace(destination)
    return destination
