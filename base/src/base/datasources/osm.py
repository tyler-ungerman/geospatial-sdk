"""Bounded OSM highway extraction from an Overpass API instance."""

from __future__ import annotations

import re
import math
import json
import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path
import xml.etree.ElementTree as ET
from urllib.parse import urlencode
from collections.abc import Sequence

from base.geo_math import bbox_from_radius
from base.schemas import BoundingBox, GeoPoint
from .http import FetchError, SourceUnavailable, Transport, http_get

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_ENDPOINTS = (
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OSM_USER_AGENT = "orbid-geospatial-research/0.1"
TILE_DEGREES = 0.02
TILE_CACHE_VERSION = 3
_TILE_LOCKS: dict[tuple, threading.Lock] = {}
_TILE_LOCKS_GUARD = threading.Lock()
_TILE_METRICS = {"hits": 0, "misses": 0, "network_requests": 0, "fetch_latency_seconds": 0.0}


def tile_cache_metrics(*, reset: bool = False) -> dict:
    """Return process-local tile counters (and optionally reset the counters)."""
    with _TILE_LOCKS_GUARD:
        result = dict(_TILE_METRICS)
        if reset:
            for key in _TILE_METRICS:
                _TILE_METRICS[key] = 0 if key != "fetch_latency_seconds" else 0.0
        return result


def _record_tile_metric(name: str, value: float = 1, *, query_metrics: dict | None = None) -> None:
    """Update legacy process metrics and an optional request-scoped counter."""
    with _TILE_LOCKS_GUARD:
        _TILE_METRICS[name] += value
        if query_metrics is not None:
            query_metrics[name] = query_metrics.get(name, 0) + value


def _tile_bounds(bounds: BoundingBox):
    # floor-based indexing works for negative coordinates. A max exactly on a
    # tile edge belongs to the tile on its left; min edges belong to their tile.
    eps = 1e-12
    ix0 = math.floor(bounds.min_lon / TILE_DEGREES)
    ix1 = math.floor((bounds.max_lon - eps) / TILE_DEGREES)
    iy0 = math.floor(bounds.min_lat / TILE_DEGREES)
    iy1 = math.floor((bounds.max_lat - eps) / TILE_DEGREES)
    for iy in range(iy0, iy1 + 1):
        for ix in range(ix0, ix1 + 1):
            yield iy, ix, BoundingBox(min_lat=iy*TILE_DEGREES, min_lon=ix*TILE_DEGREES,
                max_lat=(iy+1)*TILE_DEGREES, max_lon=(ix+1)*TILE_DEGREES)


def _intersects_bounds(feature: dict, bounds: BoundingBox) -> bool:
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    stack = list(coords)
    points = []
    while stack:
        value = stack.pop()
        if isinstance(value, (list, tuple)) and len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
            points.append(value)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    if not points:
        return False
    return (min(p[0] for p in points) <= bounds.max_lon and max(p[0] for p in points) >= bounds.min_lon
            and min(p[1] for p in points) <= bounds.max_lat and max(p[1] for p in points) >= bounds.min_lat)


def _cached_tile(tile: BoundingBox, kinds: list[str], *, client, limit: int, endpoints, cache_dir: Path,
                 offline: bool | None = None, refresh: bool = False,
                 query_metrics: dict | None = None):
    endpoint_list = [endpoints] if isinstance(endpoints, str) else list(endpoints or OVERPASS_ENDPOINTS)
    endpoint_digest = hashlib.sha256("\n".join(endpoint_list).encode()).hexdigest()[:16]
    key = ("|".join(sorted(kinds)), round(tile.min_lat/TILE_DEGREES),
           round(tile.min_lon/TILE_DEGREES), limit, endpoint_digest)
    with _TILE_LOCKS_GUARD:
        lock = _TILE_LOCKS.setdefault(key, threading.Lock())
    with lock:  # single-flight for concurrent requests of the same tile
        path = cache_dir / f"v{TILE_CACHE_VERSION}_{key[0].replace('|','_')}_{key[1]}_{key[2]}_{key[3]}_{key[4]}.json"
        legacy_path = cache_dir / f"v2_{key[0].replace('|','_')}_{key[1]}_{key[2]}_{key[3]}.json"
        read_path = legacy_path if not path.is_file() and endpoint_list == list(OVERPASS_ENDPOINTS) else path
        if not refresh and read_path.is_file():
            try:
                cached = json.loads(read_path.read_text(encoding="utf-8"))
                if cached.get("type") == "FeatureCollection":
                    _record_tile_metric("hits", query_metrics=query_metrics)
                    if read_path == legacy_path:
                        cached["_completeness_verified"] = False
                        cached["coverage_reason"] = "legacy cache lacks completeness verification"
                    return cached
            except (OSError, ValueError):
                pass
        if offline if offline is not None else os.getenv("GEOSPATIAL_OSM_OFFLINE", "0") == "1":
            _record_tile_metric("misses", query_metrics=query_metrics)
            raise SourceUnavailable(f"OSM tile unavailable in offline cache: {path.name}")
        _record_tile_metric("misses", query_metrics=query_metrics)
        _record_tile_metric("network_requests", query_metrics=query_metrics)
        started = time.monotonic()
        try:
            result = _fetch_roads_bbox(tile, kinds, client=client, limit=limit, endpoint=endpoint_list)
        finally:
            _record_tile_metric("fetch_latency_seconds", time.monotonic()-started,
                                query_metrics=query_metrics)
        # Success only; failures never create an empty cache entry.
        if result.get("coverage_complete") and result.get("features") is not None:
            result.setdefault("_retrieved_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        if result.get("features") is not None and result.get("coverage_complete", True) and not result.get("errors"):
            cache_dir.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=cache_dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(result, stream)
                os.replace(name, path)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
        return result


def fetch_roads(bounds: BoundingBox, highway_types: list[str] | None = None, *,
                client: Transport | None = None, limit: int = 2000,
                endpoint: str | Sequence[str] | None = None,
                cache_dir: str | Path | None = None, offline: bool | None = None,
                refresh: bool = False, query_metrics: dict | None = None) -> dict:
    """Fetch cached 0.02° tiles covering bounds; merge and deduplicate features.

    ``limit`` caps each tile response, not the merged window. A saturated tile
    may still be incomplete; callers needing guaranteed coverage need an
    untruncated local extract.
    """
    kinds = highway_types if highway_types is not None else ["motorway", "trunk", "primary", "secondary"]
    directory = Path(cache_dir) if cache_dir is not None else Path.home()/".cache"/"geospatial-base"/"osm-tiles"
    merged, errors, saturated_tiles, tile_records, failed_tile_ids = {}, [], [], [], []
    succeeded_tiles = 0
    for iy, ix, tile in _tile_bounds(bounds):
        tile_id = f"osm:{iy}:{ix}"
        clipped_bounds = BoundingBox(min_lat=max(bounds.min_lat, tile.min_lat), min_lon=max(bounds.min_lon, tile.min_lon),
                                    max_lat=min(bounds.max_lat, tile.max_lat), max_lon=min(bounds.max_lon, tile.max_lon))
        try:
            tile_data = _cached_tile(tile, kinds, client=client, limit=limit,
                                     endpoints=endpoint, cache_dir=directory, offline=offline, refresh=refresh,
                                     query_metrics=query_metrics)
        except SourceUnavailable as exc:
            errors.append(str(exc))
            failed_tile_ids.append(tile_id)
            tile_records.append({"tile_id": tile_id, "bounds": clipped_bounds.model_dump(), "status": "failed", "error": str(exc)})
            continue
        succeeded_tiles += 1
        errors.extend(tile_data.get("errors", []))
        saturated = len(tile_data.get("features", [])) >= limit
        verified = bool(tile_data.get("_completeness_verified", False))
        tile_complete = verified and bool(tile_data.get("coverage_complete", True)) and not saturated and not tile_data.get("errors")
        if saturated:
            saturated_tiles.append((round(tile.min_lat / TILE_DEGREES),
                                    round(tile.min_lon / TILE_DEGREES)))
        tile_records.append({"tile_id": tile_id, "bounds": clipped_bounds.model_dump(), "status": "complete" if tile_complete else "partial",
                             "feature_count": len(tile_data.get("features", [])),
                             "empty": not bool(tile_data.get("features")),
                             "saturated": saturated,
                             "retrieved_at": tile_data.get("_retrieved_at"),
                             "reason": tile_data.get("coverage_reason") or ("response reached per-tile limit" if saturated else (None if verified else "cached response lacks verification metadata")),
                             "content_sha256": __import__("hashlib").sha256(
                                 json.dumps(tile_data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()})
        for feature in tile_data.get("features", []):
            fid = str(feature.get("id") or json.dumps(feature, sort_keys=True))
            if _intersects_bounds(feature, bounds):
                merged[fid] = feature
    # `limit` bounds each upstream tile response. Truncating the merged window
    # after tile traversal discards roads based on tile order, even when every
    # required tile was successfully cached. Keep all deduplicated features.
    coverage_complete = bool(tile_records) and not errors and not saturated_tiles and all(r["status"] == "complete" for r in tile_records)
    if failed_tile_ids and succeeded_tiles == 0:
        exc = SourceUnavailable("OSM tile fetch failed for every requested tile: " + " | ".join(errors))
        exc.tile_records = tile_records
        exc.failed_tile_ids = failed_tile_ids
        exc.errors = errors
        raise exc
    return {"type": "FeatureCollection", "features": list(merged.values()),
            "coverage_complete": coverage_complete, "errors": errors,
            "possibly_truncated_tiles": saturated_tiles, "tile_records": tile_records,
            "failed_tile_ids": failed_tile_ids,
            "coverage_reasons": [r["reason"] for r in tile_records if r.get("reason")],
            "data_status": "complete_empty" if coverage_complete and not merged else ("complete" if coverage_complete else ("failed" if failed_tile_ids and not merged else "partial"))}


def _fetch_roads_bbox(bounds: BoundingBox, highway_types: list[str] | None = None, *,
                client: Transport | None = None, limit: int = 2000,
                endpoint: str | Sequence[str] | None = None) -> dict:
    """Return GeoJSON FeatureCollection of way line geometries from Overpass.

    Bounds must be explicit; `limit` limits locally returned features, not Overpass work.
    Respect public API rate limits; use a dedicated instance for large jobs.
    """
    kinds = highway_types if highway_types is not None else ["motorway", "trunk", "primary", "secondary"]
    if not kinds or any(re.fullmatch(r"[a-z_]+", k) is None for k in kinds):
        raise ValueError("Highway types must be simple OSM tag values")
    if not 0 < limit <= 10_000:
        raise ValueError("limit must be in [1,10000]")
    approximate_area_km2 = ((bounds.max_lat - bounds.min_lat) *
        (bounds.max_lon - bounds.min_lon) * 111.32**2 *
        abs(math.cos(math.radians((bounds.min_lat + bounds.max_lat)/2))))
    if approximate_area_km2 > 2500:
        raise ValueError("Public Overpass queries are limited to ~2500 km²; use extracts for larger areas")
    bbox = ",".join(str(v) for v in bounds.overpass_tuple())
    regex = "|".join(sorted(set(kinds)))
    query = (f'[out:json][timeout:25];(way["highway"~"^({regex})$"]({bbox});'
             f'way["building"]({bbox});way["natural"~"^(water|coastline|wood|scrub|grassland)$"]({bbox});'
             f'way["landuse"~"^(forest|grass|meadow|farmland|orchard|vineyard|recreation_ground|reservoir)$"]({bbox});'
             f'way["waterway"]({bbox});way["leisure"="park"]({bbox});'
             f'relation["type"="multipolygon"]({bbox}););out body geom;')
    endpoints = [endpoint] if isinstance(endpoint, str) else list(endpoint or OVERPASS_ENDPOINTS)
    if not endpoints:
        raise ValueError("At least one Overpass endpoint is required")
    failures: list[str] = []
    data = None
    for target in endpoints:
        try:
            data = http_get(client, f"{target}?{urlencode({'data': query})}",
                            headers={"User-Agent": OSM_USER_AGENT}, timeout=30).json()
            if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
                raise FetchError("Overpass response has no elements array")
            break
        except Exception as exc:
            failures.append(f"{target}: {type(exc).__name__}: {exc}")
            data = None
    if data is None:
        try:
            fallback = _fetch_standard_map(bounds, kinds, client=client, limit=limit)
            return fallback
        except Exception as exc:
            failures.append(f"OpenStreetMap map API: {type(exc).__name__}: {exc}")
            raise SourceUnavailable("All configured road sources failed: " + " | ".join(failures)) from exc
    features = []
    parse_errors = [f"Overpass remark: {data['remark']}" for _ in [0] if data.get("remark")]
    for way in data["elements"]:
        if not isinstance(way, dict) or way.get("type") != "way":
            continue
        try:
            tags = dict(way.get("tags") or {})
        except (TypeError, ValueError):
            parse_errors.append(f"Malformed tags for OSM way {way.get('id', '<missing-id>')}")
            continue
        if tags.get("highway") is None and not any(k in tags for k in ("building", "natural", "landuse", "waterway", "leisure")):
            continue
        if not isinstance(way.get("geometry"), list) or not way.get("id"):
            parse_errors.append(f"Malformed relevant way element {way.get('id', '<missing-id>')}")
            continue
        vertices = way["geometry"]
        try:
            coords = [[float(p["lon"]), float(p["lat"])] for p in vertices]
        except (KeyError, TypeError, ValueError):
            parse_errors.append(f"Malformed geometry for OSM way {way.get('id')}")
            continue
        if len(coords) < 2 or any(not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in coords):
            parse_errors.append(f"Invalid coordinate geometry for OSM way {way.get('id')}")
            continue
        polygon = len(coords) >= 4 and coords[0] == coords[-1] and (
            "building" in tags or tags.get("natural") == "water" or tags.get("landuse") in {"forest", "grass", "meadow", "farmland", "reservoir"})
        geometry = {"type": "Polygon", "coordinates": [coords]} if polygon else {
            "type": "LineString", "coordinates": coords}
        props = {**tags, "osm_nodes": way.get("nodes", [])}
        features.append({"type": "Feature", "id": f"osm-way-{way.get('id')}",
                         "geometry": geometry, "properties": props})
    for relation in data["elements"]:
        if not isinstance(relation, dict) or relation.get("type") != "relation":
            continue
        try:
            tags = dict(relation.get("tags") or {})
        except (TypeError, ValueError):
            parse_errors.append(f"Malformed tags for OSM relation {relation.get('id', '<missing-id>')}")
            continue
        if tags.get("type") != "multipolygon" or not any(k in tags for k in ("building", "natural", "landuse", "leisure")):
            continue
        if not relation.get("id") or not isinstance(relation.get("members"), list):
            parse_errors.append(f"Malformed relevant multipolygon relation {relation.get('id', '<missing-id>')}")
            continue
        outer, inner = [], []
        relation_invalid = False
        for member in relation.get("members", []):
            if not isinstance(member, dict):
                relation_invalid = True
                break
            if member.get("type") != "way":
                continue
            if member.get("role") not in ("outer", "inner") or not isinstance(member.get("geometry"), list):
                relation_invalid = True
                break
            try:
                ring = [[float(p["lon"]), float(p["lat"])] for p in member["geometry"]]
            except (KeyError, TypeError, ValueError):
                relation_invalid = True
                break
            if len(ring) < 4 or ring[0] != ring[-1] or any(not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in ring):
                relation_invalid = True
                break
            (outer if member.get("role") == "outer" else inner).append(ring)
        if relation_invalid or not outer:
            parse_errors.append(f"Incomplete geometry for OSM relation {relation.get('id')}")
            continue
        # Preserve all outer rings and retain inner rings as holes on the first
        # polygon; exact OSM ring stitching is intentionally left to consumers.
        polygons = [[ring, *inner] if index == 0 else [ring] for index, ring in enumerate(outer)]
        geometry = {"type": "Polygon", "coordinates": polygons[0]} if len(polygons) == 1 else {
            "type": "MultiPolygon", "coordinates": polygons}
        features.append({"type": "Feature", "id": f"osm-relation-{relation.get('id')}",
                         "geometry": geometry, "properties": tags})
    features.sort(key=lambda feature: (0 if (feature.get("properties") or {}).get("highway") else 1,
                                       str(feature.get("id", ""))))
    saturated = len(features) >= limit
    if saturated:
        parse_errors.append("response reached per-tile feature limit")
    return {"type": "FeatureCollection", "features": features[:limit],
            "coverage_complete": not parse_errors, "_completeness_verified": True,
            "errors": parse_errors, "coverage_reason": parse_errors[0] if parse_errors else None}


def _parse_map_xml(body: bytes, highway_types: set[str], limit: int, *, errors: list[str] | None = None) -> list[dict]:
    root = ET.fromstring(body)
    nodes = {node.get("id"): [float(node.get("lon")), float(node.get("lat"))]
             for node in root.findall("node") if node.get("id") and node.get("lat") and node.get("lon")}
    features = []
    for way in root.findall("way"):
        tags = {tag.get("k"): tag.get("v") for tag in way.findall("tag") if tag.get("k") and tag.get("v")}
        if tags.get("highway") not in highway_types and not any(k in tags for k in ("building", "natural", "landuse", "waterway", "leisure")):
            continue
        refs = [nd.get("ref") for nd in way.findall("nd")]
        coords = [nodes[ref] for ref in refs if ref in nodes]
        if len(coords) != len(refs) and errors is not None:
            errors.append(f"Missing node geometry for relevant OSM way {way.get('id', '<missing-id>')}")
        if len(coords) >= 2:
            is_polygon = len(coords) >= 4 and coords[0] == coords[-1] and (
                "building" in tags or tags.get("natural") == "water" or tags.get("landuse") in {"forest", "grass", "meadow", "farmland", "reservoir"})
            geometry = {"type": "Polygon", "coordinates": [coords]} if is_polygon else {"type": "LineString", "coordinates": coords}
            tags["osm_nodes"] = [nd.get("ref") for nd in way.findall("nd")]
            features.append({"type": "Feature", "id": f"osm-way-{way.get('id')}",
                "geometry": geometry, "properties": tags})
    features.sort(key=lambda feature: (0 if feature["properties"].get("highway") else 1,
                                       feature["id"]))
    return features[:limit]


def _fetch_standard_map(bounds: BoundingBox, highway_types: list[str], *,
                        client: Transport | None, limit: int, depth: int = 0) -> dict:
    """Fallback to the core OSM map API, splitting oversized map requests recursively."""
    bbox = f"{bounds.min_lon},{bounds.min_lat},{bounds.max_lon},{bounds.max_lat}"
    url = f"https://api.openstreetmap.org/api/0.6/map?{urlencode({'bbox': bbox})}"
    try:
        response = http_get(client, url, headers={"User-Agent": OSM_USER_AGENT}, timeout=30)
        errors: list[str] = []
        features = _parse_map_xml(response.body, set(highway_types), limit, errors=errors)
        if len(features) >= limit:
            errors.append("response reached per-tile feature limit")
        return {"type": "FeatureCollection", "features": features[:limit],
                "coverage_complete": not errors, "_completeness_verified": True, "errors": errors}
    except SourceUnavailable as exc:
        if depth >= 3 or "too many nodes" not in str(exc).lower():
            raise
        # Preserve complete requested coverage under the core map API's
        # 50k-node cap by recursively splitting oversized boxes. A successful
        # response for a center patch is not a complete response for its tile.
        mid_lat = (bounds.min_lat+bounds.max_lat)/2
        mid_lon = (bounds.min_lon+bounds.max_lon)/2
        tiles = [
            BoundingBox(min_lat=bounds.min_lat, min_lon=bounds.min_lon, max_lat=mid_lat, max_lon=mid_lon),
            BoundingBox(min_lat=bounds.min_lat, min_lon=mid_lon, max_lat=mid_lat, max_lon=bounds.max_lon),
            BoundingBox(min_lat=mid_lat, min_lon=bounds.min_lon, max_lat=bounds.max_lat, max_lon=mid_lon),
            BoundingBox(min_lat=mid_lat, min_lon=mid_lon, max_lat=bounds.max_lat, max_lon=bounds.max_lon),
        ]
        features, errors = [], []
        children_complete = True
        for tile in tiles:
            try:
                tile_result = _fetch_standard_map(tile, highway_types, client=client,
                                                  limit=limit, depth=depth+1)
                features.extend(tile_result["features"])
                errors.extend(tile_result.get("errors", []))
                if not tile_result.get("coverage_complete", False):
                    children_complete = False
                    if not tile_result.get("errors"):
                        errors.append("child map split has incomplete coverage")
            except SourceUnavailable as child_error:
                errors.append(str(child_error))
                children_complete = False
        deduplicated = {feature["id"]: feature for feature in features}
        ordered = sorted(deduplicated.values(), key=lambda feature: (
            0 if (feature.get("properties") or {}).get("highway") else 1, feature["id"]))
        saturated = len(ordered) >= limit
        if saturated:
            errors.append("recursive map split reached per-tile feature limit")
        complete = children_complete and not errors and not saturated
        return {"type": "FeatureCollection", "features": ordered[:limit],
                "coverage_complete": complete, "_completeness_verified": True,
                "errors": errors, "coverage_reason": errors[0] if errors else None}
