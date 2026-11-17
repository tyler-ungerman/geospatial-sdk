"""GDACS disaster-alert source."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from base.datasources.http import FetchError, JsonRecord, Transport, coordinates, http_get
from base.datasources.registry import register_source

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
EVENT_LABELS = {"EQ": "Earthquake", "TC": "Tropical Cyclone", "FL": "Flood", "VO": "Volcanic Activity", "DR": "Drought", "WF": "Wildfire"}


@register_source("gdacs", kind="events")
def fetch_gdacs(
    *, client: Transport | None = None, now: datetime | None = None, limit: int = 50, lookback_days: int = 7
) -> list[JsonRecord]:
    """GDACS disaster alerts over a configurable recent UTC day window."""
    if lookback_days < 1:
        raise ValueError("lookback_days must be positive")
    today = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date()
    params = {
        "alertlevel": "Green;Orange;Red", "eventlist": "EQ;TC;FL;VO;DR;WF",
        "fromDate": (today - timedelta(days=lookback_days)).isoformat(),
        "toDate": today.isoformat(), "limit": str(limit),
    }
    url = f"{GDACS_URL}?{urlencode(params, safe=';')}"
    data = http_get(client, url, headers={"Accept": "application/json"}).json()
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise FetchError("GDACS response has no features array")
    events: list[JsonRecord] = []
    for feature in data["features"]:
        if len(events) >= limit:
            break
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        loc = coordinates(geom.get("coordinates"))
        if loc is None or not isinstance(props, dict):
            continue
        lat, lng = loc
        level = str(props.get("alertlevel") or "").lower()
        event_type = str(props.get("eventtype") or "").upper()
        label = EVENT_LABELS.get(event_type, "Disaster")
        severity = "critical" if level == "red" else "high" if level == "orange" else "medium"
        population = props.get("population")
        if not isinstance(population, dict):
            population = {}
        events.append({
            "category": "disaster", "severity": severity,
            "title": f"{label} — {props.get('name') or props.get('country') or 'Unknown'}",
            "description": props.get("description") or f"{label} alert ({level.upper()}) reported by GDACS. Population affected: {population.get('value') or 'unknown'}",
            "source": "GDACS", "country": props.get("country") or None,
            "lat": lat, "lng": lng, "location_basis": "reported",
            "url": props.get("url") or f"https://www.gdacs.org/report.aspx?eventid={props.get('eventid')}&episodeid={props.get('episodeid')}",
            "raw_data": {"eventtype": event_type, "alertlevel": level, "eventid": props.get("eventid"), "severity": props.get("severity")},
            "is_breaking": level == "red",
        })
    return events
