"""Source fetch -> normalization -> deduplication -> pluggable JSON persistence.

Legacy R2 layout retained for easier migration:
  geospatial/events/YYYY-MM-DD.json
  geospatial/entities/<source>.json
  geospatial/runs/latest.json

New normalized rows additionally include location_basis and location_uncertainty_m.
An absent radius explicitly means unknown, NOT a precise location.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping

from base.datasources import FetchReport, JsonRecord, Transport, fetch_all, get_source
from base.datasources.http import coordinates, to_number, utc_iso
from base.datasources.osint.classification import CATEGORIES, SEVERITIES
from base.schemas import GeoEntity, GeoEvent, GeoPoint
from .backends import JSONStore, R2Store, validate_key

EVENTS_PREFIX = "geospatial/events"
ENTITIES_PREFIX = "geospatial/entities"
RUN_SUMMARY_KEY = "geospatial/runs/latest.json"


def event_id(record: Mapping[str, Any]) -> str:
    """Stable id from source id, canonical article URL, or observed detection details.

    NASA FIRMS URLs point to the same map for every fire, so coordinates/time are
    included for such non-unique links. This avoids collapsing all FIRMS fires.
    """
    if record.get("id"):
        return str(record["id"])
    source = str(record.get("source") or "unknown")
    url = str(record.get("url") or "")
    raw = record.get("raw_data") or {}
    if source == "NASA FIRMS":
        identity = "|".join(map(str, (source, record.get("lat"), record.get("lng"),
                                        raw.get("date"), raw.get("time"))))
    else:
        identity = f"{source}|{url or record.get('title')}"
    return hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest()[:20]


def _point(record: Mapping[str, Any]) -> GeoPoint | None:
    loc = coordinates([record.get("lng"), record.get("lat")])
    if loc is None:
        return None
    basis = record.get("location_basis") or "reported"
    if basis not in ("reported", "inferred", "derived"):
        basis = "reported"
    radius = to_number(record.get("location_uncertainty_m"))
    if radius is not None and radius < 0:
        radius = None
    return GeoPoint(lat=loc[0], lng=loc[1], basis=basis, uncertainty_m=radius)


def normalize_event(record: JsonRecord, ingested_at: str) -> JsonRecord | None:
    title = str(record.get("title") or "").strip()
    if not title:
        return None
    point = _point(record)
    normalized = {
        "id": event_id(record),
        "category": record.get("category") if record.get("category") in CATEGORIES else "political",
        "severity": record.get("severity") if record.get("severity") in SEVERITIES else "medium",
        "title": title, "description": record.get("description"),
        "source": record.get("source") or "Unknown", "country": record.get("country"),
        "lat": point.lat if point else None, "lng": point.lng if point else None,
        "location_basis": point.basis if point else None,
        "location_uncertainty_m": point.uncertainty_m if point else None,
        "url": record.get("url") or None, "raw_data": record.get("raw_data") or {},
        "is_breaking": record.get("is_breaking") is True, "ingested_at": ingested_at,
    }
    # Validation at ingress; previous consumer-facing field layout is retained.
    GeoEvent(id=normalized["id"], title=title, source=str(normalized["source"]),
             category=normalized["category"], severity=normalized["severity"],
             location=point, ingested_at=datetime.fromisoformat(ingested_at.replace("Z", "+00:00")))
    return normalized


def normalize_entity(record: JsonRecord, ingested_at: str) -> JsonRecord | None:
    if not record.get("id") or (point := _point(record)) is None:
        return None
    normalized = {**record, "lat": point.lat, "lng": point.lng,
                  "location_basis": point.basis, "location_uncertainty_m": point.uncertainty_m,
                  "ingested_at": ingested_at}
    GeoEntity(id=str(record["id"]), name=str(record.get("name") or record["id"]),
              source=str(record.get("source") or "Unknown"), location=point)
    return normalized


def merge_by_id(existing: list[JsonRecord], incoming: list[JsonRecord]) -> list[JsonRecord]:
    merged = {record["id"]: dict(record) for record in existing}
    for record in incoming:
        prev = merged.get(record["id"])
        merged[record["id"]] = ({**prev, **record, "ingested_at": prev.get("ingested_at", record.get("ingested_at"))}
                                if prev is not None else record)
    return list(merged.values())


def normalize_events(records: list[JsonRecord], ingested_at: str) -> list[JsonRecord]:
    return merge_by_id([], [item for row in records if (item := normalize_event(row, ingested_at))])


def normalize_entities(records: list[JsonRecord], ingested_at: str) -> list[JsonRecord]:
    return merge_by_id([], [item for row in records if (item := normalize_entity(row, ingested_at))])


def run_pipeline(names: list[str] | None = None, *, kind: str | None = None,
                 options: Mapping[str, Mapping[str, Any]] | None = None,
                 client: Transport | None = None, store: JSONStore | None = None,
                 now: datetime | None = None, prefix: str = "geospatial") -> JsonRecord:
    """Fetch and store. Inject store/client for tests; never needs R2 to import.

    Never replaces stored data on FetchError. To prevent live writes, pass
    LocalJSONStore or RunScopedStore; default is explicitly the R2 backend.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    validate_key(prefix)
    store = store if store is not None else R2Store()
    ingested_at = utc_iso(now)
    report: FetchReport = fetch_all(names, kind=kind, client=client, options=options)  # type: ignore[arg-type]
    incoming_events: list[JsonRecord] = []
    entity_counts: dict[str, int] = {}
    for source_name, records in report.records.items():
        if get_source(source_name).kind == "events":
            incoming_events.extend(normalize_events(records, ingested_at))
        else:
            entities = normalize_entities(records, ingested_at)
            store.write_json(f"{prefix}/entities/{source_name}.json", entities)
            entity_counts[source_name] = len(entities)

    events_new = events_total_today = 0
    incoming_events = merge_by_id([], incoming_events)
    if incoming_events:
        key = f"{prefix}/events/{now.astimezone(timezone.utc):%Y-%m-%d}.json"
        old = store.read_json(key, default=[])
        if not isinstance(old, list):
            raise ValueError(f"Stored events object at {key} is not a list")
        merged = merge_by_id(old, incoming_events)
        store.write_json(key, merged)
        events_new = len(merged) - len(old)
        events_total_today = len(merged)
    summary = {"ran_at": ingested_at, "sources_ok": sorted(report.records), "errors": report.errors,
               "events_fetched": len(incoming_events), "events_new": events_new,
               "events_total_today": events_total_today, "entities": entity_counts}
    store.write_json(f"{prefix}/runs/latest.json", summary)
    return summary
