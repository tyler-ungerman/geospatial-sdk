"""USGS earthquake source."""

from __future__ import annotations

from base.datasources.http import FetchError, JsonRecord, Transport, coordinates, http_get, to_number
from base.datasources.registry import register_source

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_day.geojson"


@register_source("usgs", kind="events")
def fetch_earthquakes(*, client: Transport | None = None, limit: int = 50) -> list[JsonRecord]:
    """USGS M2.5+ earthquakes in the last 24 hours, newest feed order first."""
    data = http_get(client, USGS_URL).json()
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise FetchError("USGS response has no features array")
    events: list[JsonRecord] = []
    for feature in data["features"][:limit]:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        loc = coordinates(geometry.get("coordinates"))
        if loc is None or not isinstance(props, dict):
            continue
        lat, lng = loc
        coords = geometry["coordinates"]
        mag = to_number(props.get("mag")) or 0.0
        depth = to_number(coords[2]) if len(coords) > 2 else None
        place = str(props.get("place") or "Unknown")
        severity = "critical" if mag >= 7 else "high" if mag >= 5.5 else "medium" if mag >= 4 else "low"
        events.append({
            "category": "disaster", "severity": severity,
            "title": f"M{mag:.1f} Earthquake — {place}",
            "description": (
                f"Magnitude {mag:.1f} earthquake detected. "
                f"Depth: {depth:.1f}km. " if depth is not None else
                f"Magnitude {mag:.1f} earthquake detected. Depth: ?km. "
            ) + ("⚠️ Tsunami warning issued." if props.get("tsunami") else ""),
            "source": "USGS", "country": place.split(", ")[-1],
            "lat": lat, "lng": lng, "location_basis": "reported", "url": props.get("url") or None,
            "raw_data": {"mag": mag, "depth": depth, "tsunami": props.get("tsunami"), "time": props.get("time")},
            "is_breaking": mag >= 6,
        })
    return events
