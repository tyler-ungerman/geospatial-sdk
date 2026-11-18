"""Small typed query/result contract for spatial data sources.

The contract deliberately describes completeness separately from data. An
empty, fully enumerated layer is different from an unavailable layer.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Literal

from pydantic import Field

from base.schemas import BoundingBox, Contract
from .http import HttpClient, SourceUnavailable, Transport

CoverageStatus = Literal["complete", "partial", "unknown", "failed"]


class LayerRequest(Contract):
    name: str = Field(min_length=1)
    filters: dict[str, Any] = Field(default_factory=dict)


class SpatialQuery(Contract):
    """A bounded spatial request. Region coordinates are WGS84 lon/lat."""

    region: BoundingBox
    layers: list[LayerRequest] = Field(min_length=1)
    offline: bool = False
    require_complete: bool = False
    cache_policy: Literal["read_write", "read_only", "refresh"] = "read_write"
    max_requests: int | None = Field(default=None, ge=0)


class CoverageReport(Contract):
    requested_geometry: BoundingBox
    verified_covered_geometry: list[BoundingBox] = Field(default_factory=list)
    missing_or_unknown_geometry: list[BoundingBox] = Field(default_factory=list)
    status: CoverageStatus
    truncated: bool = False
    omitted_feature_count: int | None = Field(default=None, ge=0)
    failed_tile_ids: list[str] = Field(default_factory=list)
    covered_tile_ids: list[str] = Field(default_factory=list)
    unknown_tile_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


class SourceRecord(Contract):
    provider: str
    source_id: str
    retrieved_at: datetime | None = None
    source_version: str | None = None
    request_digest: str
    content_sha256: str
    licence: str | None = "OpenStreetMap ODbL"
    attribution: str | None = "© OpenStreetMap contributors"
    cache_origin: Literal["cache", "network", "mixed", "unknown"] = "unknown"


class QueryDiagnostics(Contract):
    request_count: int = Field(default=0, ge=0)
    cache_hits: int = Field(default=0, ge=0)
    cache_misses: int = Field(default=0, ge=0)
    feature_count: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class LayerResult(Contract):
    data: dict[str, Any]
    coverage: CoverageReport
    provenance: list[SourceRecord] = Field(default_factory=list)
    diagnostics: QueryDiagnostics = Field(default_factory=QueryDiagnostics)


class IncompleteCoverageError(RuntimeError):
    """Raised when a query requiring completeness cannot verify full coverage."""

    def __init__(self, coverage: CoverageReport):
        self.coverage = coverage
        super().__init__(f"Spatial query coverage is {coverage.status}: " + "; ".join(coverage.reasons))


def query_osm_roads(query: SpatialQuery, *, client=None, limit: int = 2000,
                    endpoint=None, cache_dir=None) -> LayerResult:
    """Run the OSM road adapter through the typed query contract."""
    from base.datasources import osm

    if query.cache_policy == "refresh" and query.offline:
        raise ValueError("cache_policy='refresh' cannot be combined with offline=True")
    if query.cache_policy == "read_only" and not query.offline:
        # Read-only is still offline by definition; this prevents accidental
        # network work when callers only intended cache replay.
        offline = True
    else:
        offline = query.offline
    roads_request = next((layer for layer in query.layers if layer.name == "osm.roads"), None)
    if roads_request is None:
        raise ValueError("query_osm_roads requires a layer named 'osm.roads'")
    highway_types = roads_request.filters.get("highway_types")
    if not 0 < limit <= 10_000:
        raise ValueError("limit must be in [1, 10000]")
    errors: list[str] = []
    request_counter = {"count": 0}
    query_metrics = {"hits": 0, "misses": 0, "network_requests": 0, "fetch_latency_seconds": 0.0}

    class BudgetTransport:
        def __init__(self, upstream: Transport, maximum: int | None):
            self.upstream, self.maximum = upstream, maximum

        def request(self, method, url, *, headers=None, json_body=None, timeout=15):
            if self.maximum is not None and request_counter["count"] >= self.maximum:
                raise SourceUnavailable(f"request budget exhausted ({self.maximum})")
            request_counter["count"] += 1
            return self.upstream.request(method, url, headers=headers, json_body=json_body, timeout=timeout)

    transport = BudgetTransport(client or HttpClient(), query.max_requests)
    try:
        data = osm.fetch_roads(query.region, highway_types, client=transport, limit=limit,
                               endpoint=endpoint, cache_dir=cache_dir, offline=offline,
                               refresh=query.cache_policy == "refresh", query_metrics=query_metrics)
    except Exception as exc:
        if not isinstance(exc, (OSError, RuntimeError)):
            raise
        errors.append(f"{type(exc).__name__}: {exc}")
        data = {"type": "FeatureCollection", "features": [], "coverage_complete": False,
                "errors": errors, "tile_records": getattr(exc, "tile_records", []),
                "failed_tile_ids": getattr(exc, "failed_tile_ids", []), "coverage_reasons": errors,
                "data_status": "failed"}
    tile_records = data.get("tile_records", [])
    complete_tiles = [r for r in tile_records if r.get("status") == "complete"]
    complete_ids = [r["tile_id"] for r in complete_tiles]
    failed_ids = list(data.get("failed_tile_ids", []))
    errors.extend(data.get("errors", []))
    unknown_tiles = [r for r in tile_records if r.get("status") in ("partial", "failed")]
    unknown_ids = [r["tile_id"] for r in unknown_tiles]
    complete_bounds = [BoundingBox(**r["bounds"]) for r in complete_tiles if r.get("bounds")]
    unknown_bounds = [BoundingBox(**r["bounds"]) for r in unknown_tiles if r.get("bounds")]
    if failed_ids:
        status: CoverageStatus = "partial" if complete_ids or data["features"] else "failed"
    elif errors and not tile_records:
        status = "failed"
    elif unknown_ids:
        status = "partial"
    elif data.get("coverage_complete"):
        status = "complete"
    else:
        status = "unknown"
    reasons = list(data.get("coverage_reasons", [])) + errors
    coverage = CoverageReport(requested_geometry=query.region,
        verified_covered_geometry=complete_bounds, missing_or_unknown_geometry=unknown_bounds, status=status,
        truncated=bool(data.get("possibly_truncated_tiles")), failed_tile_ids=failed_ids,
        covered_tile_ids=complete_ids, unknown_tile_ids=unknown_ids, reasons=sorted(set(reasons)))
    if query.require_complete and coverage.status != "complete":
        raise IncompleteCoverageError(coverage)
    effective_endpoints = [endpoint] if isinstance(endpoint, str) else list(endpoint or osm.OVERPASS_ENDPOINTS)
    request_identity = {"query": query.model_dump(exclude_none=True), "endpoint_sha256": sha256(
        "\n".join(effective_endpoints).encode()).hexdigest(), "limit": limit,
        "adapter": "osm.tile.v3"}
    canonical_request = json.dumps(request_identity, sort_keys=True, separators=(",", ":"))
    request_digest = sha256(canonical_request.encode()).hexdigest()
    content_digest = sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    metrics = query_metrics
    cache_origin = ("mixed" if request_counter["count"] and metrics["hits"] else
                    "network" if request_counter["count"] else
                    "cache" if metrics["hits"] else "unknown")
    source_times = [r.get("retrieved_at") for r in tile_records if r.get("retrieved_at")]
    retrieved_at = min(source_times) if source_times else None
    provenance = [SourceRecord(provider="OpenStreetMap", source_id="osm.roads",
        retrieved_at=retrieved_at, request_digest=request_digest,
        content_sha256=content_digest, cache_origin=cache_origin)]
    diagnostics = QueryDiagnostics(request_count=request_counter["count"], cache_hits=metrics["hits"],
        cache_misses=metrics["misses"], feature_count=len(data.get("features", [])), errors=errors)
    return LayerResult(data=data, coverage=coverage, provenance=provenance, diagnostics=diagnostics)
