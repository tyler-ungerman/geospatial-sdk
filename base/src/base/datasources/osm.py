"""Bounded OSM highway extraction from an Overpass API instance."""

from __future__ import annotations

import re
import math
from urllib.parse import urlencode

from base.schemas import BoundingBox
from .http import FetchError, Transport, http_get

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def fetch_roads(bounds: BoundingBox, highway_types: list[str] | None = None, *,
                client: Transport | None = None, limit: int = 2000,
                endpoint: str = OVERPASS_URL) -> dict:
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
    query = f'[out:json][timeout:25];way["highway"~"^({regex})$"]({bbox});out geom;'
    data = http_get(client, f"{endpoint}?{urlencode({'data': query})}", timeout=30).json()
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list):
        raise FetchError("Overpass response has no elements array")
    features = []
    for way in data["elements"]:
        if len(features) >= limit:
            break
        if not isinstance(way, dict) or way.get("type") != "way" or not isinstance(way.get("geometry"), list):
            continue
        vertices = way["geometry"]
        try:
            coords = [[float(p["lon"]), float(p["lat"])] for p in vertices]
        except (KeyError, TypeError, ValueError):
            continue
        if len(coords) < 2 or any(not -180 <= p[0] <= 180 or not -90 <= p[1] <= 90 for p in coords):
            continue
        features.append({"type": "Feature", "id": f"osm-way-{way.get('id')}",
                         "geometry": {"type": "LineString", "coordinates": coords},
                         "properties": dict(way.get("tags") or {})})
    return {"type": "FeatureCollection", "features": features}
