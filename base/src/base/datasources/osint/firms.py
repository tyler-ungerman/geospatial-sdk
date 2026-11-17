"""NASA FIRMS active-fire source."""

from __future__ import annotations

import csv
import io

from base.datasources.http import FetchError, JsonRecord, Transport, coordinates, http_get, to_number
from base.datasources.registry import register_source

FIRMS_URL = "https://firms.modaps.eosdis.nasa.gov/data/active_fire/modis-c6.1/csv/MODIS_C6_1_Global_24h.csv"


@register_source("firms", kind="events")
def fetch_firms(*, client: Transport | None = None, limit: int = 80, min_confidence: int = 80) -> list[JsonRecord]:
    """NASA FIRMS MODIS recent fire detections, ordered by radiative power."""
    response = http_get(client, FIRMS_URL, timeout=30)
    reader = csv.DictReader(io.StringIO(response.text))
    required = {"latitude", "longitude", "brightness", "confidence", "acq_date", "acq_time", "frp"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise FetchError("NASA FIRMS CSV has missing or invalid headers")
    fires: list[JsonRecord] = []
    for row in reader:
        loc = coordinates([row.get("longitude"), row.get("latitude")])
        conf = to_number(row.get("confidence"))
        if loc is None or conf is None or conf < min_confidence:
            continue
        lat, lng = loc
        fires.append({
            "lat": lat, "lng": lng,
            "brightness": to_number(row.get("brightness")),
            "confidence": int(conf),
            "date": row.get("acq_date"), "time": row.get("acq_time"),
            "frp": to_number(row.get("frp")) or 0.0,
        })
    fires.sort(key=lambda f: f["frp"], reverse=True)
    events: list[JsonRecord] = []
    for f in fires[:limit]:
        frp = f["frp"]
        severity = "critical" if frp >= 500 else "high" if frp >= 200 else "medium" if frp >= 50 else "low"
        brightness = f["brightness"]
        events.append({
            "category": "disaster", "severity": severity,
            "title": f"Active Wildfire — {f['lat']:.2f}°, {f['lng']:.2f}°",
            "description": (
                f"Fire radiative power: {frp:.0f} MW. "
                f"Brightness: {brightness:.0f}K. " if brightness is not None else
                f"Fire radiative power: {frp:.0f} MW. Brightness: ?K. "
            ) + f"Confidence: {f['confidence']}%. Detected: {f['date']} {f['time']}",
            "source": "NASA FIRMS", "country": None,
            "lat": f["lat"], "lng": f["lng"], "location_basis": "reported",
            "url": "https://firms.modaps.eosdis.nasa.gov/map/",
            "raw_data": f, "is_breaking": False,
        })
    return events
