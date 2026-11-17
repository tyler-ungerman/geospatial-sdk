"""OpenSky Network aircraft source (produces ``entities`` records).

The callsign-based military classification is a heuristic, not a verified
identification; callsign prefixes alone are unreliable for that purpose.
"""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urlencode

from base.datasources.http import FetchError, JsonRecord, Transport, coordinates, http_get, to_number, utc_iso
from base.datasources.registry import register_source

OPENSKY_URL = "https://opensky-network.org/api/states/all"
MILITARY_CALLSIGN = re.compile(
    r"^(RCH|DUKE|EVAC|REACH|JAKE|VALOR|CSAR|BOLT|VIPER|COBRA|HAWK|"
    r"EAGLE|FURY|BLADE|STEEL|IRON|TEAL|NATO|RRR|NAVY|AIR[FM]|BAF|"
    r"GAF|FAF|IAF|RAF|PAF|USAF|RFR|CFC|MAC|AMC)"
)


@register_source("opensky", kind="entities")
def fetch_opensky(
    *, client: Transport | None = None, lamin: float = 10, lomin: float = 25,
    lamax: float = 55, lomax: float = 75, limit: int = 200,
    now: datetime | None = None, access_token: str | None = None,
) -> list[JsonRecord]:
    """OpenSky airborne aircraft in a bounding box. 429/network failures raise SourceUnavailable."""
    if not (-90 <= lamin < lamax <= 90 and -180 <= lomin < lomax <= 180):
        raise ValueError("Invalid OpenSky bounding box")
    params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}
    url = f"{OPENSKY_URL}?{urlencode(params)}"
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    data = http_get(client, url, headers=headers, timeout=8).json()
    if not isinstance(data, dict) or "states" not in data or not (data["states"] is None or isinstance(data["states"], list)):
        raise FetchError("OpenSky response has missing or invalid states")
    entities: list[JsonRecord] = []
    timestamp = utc_iso(now)
    for s in (data.get("states") or []):
        if len(entities) >= limit:
            break
        if not isinstance(s, list) or len(s) < 11 or s[8]:
            continue
        loc = coordinates([s[5], s[6]])
        if loc is None or not s[0]:
            continue
        lat, lng = loc
        icao = str(s[0]).strip().lower()
        callsign = str(s[1]).strip() if s[1] is not None else ""
        callsign = callsign or None
        country = str(s[2] or "Unknown")
        altitude_m = to_number(s[7])
        speed_ms = to_number(s[9])
        heading = to_number(s[10])
        classification = "military" if callsign and MILITARY_CALLSIGN.match(callsign.upper()) else "civilian" if callsign else "unknown"
        entities.append({
            "id": f"opensky-{icao}", "type": "aircraft", "classification": classification,
            "name": callsign or f"ICAO:{icao.upper()}", "callsign": callsign,
            "lat": lat, "lng": lng, "location_basis": "reported", "heading": heading,
            "speed": round(speed_ms * 1.94384) if speed_ms is not None else None,
            "altitude": round(altitude_m * 3.28084) if altitude_m is not None else None,
            "source": "OpenSky Network", "details": f"Origin: {country}. Real-time ADS-B tracking.",
            "country": country, "flag_code": None,
            "threat_score": 40 if classification == "military" else 25 if classification == "unknown" else 0,
            "origin": country, "updated_at": timestamp,
        })
    return entities
