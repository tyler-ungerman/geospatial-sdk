"""Example source adapter for observations supplied by caller, not fake network data."""
from __future__ import annotations

from gspace.datasources import register_source
from gspace.schemas import GeoEvent


def fetch_observations(*, client=None, observations: list[dict] | None = None) -> list[dict]:
    """Validate caller-supplied geospatial observations without any network calls."""
    if observations is None:
        return []
    result = []
    for row in observations:
        event = GeoEvent.model_validate(row)
        result.append({"id": event.id, "title": event.title, "source": event.source,
                       "category": event.category, "severity": event.severity,
                       "lat": event.location.lat if event.location else None,
                       "lng": event.location.lng if event.location else None,
                       "location_basis": event.location.basis if event.location else None,
                       "location_uncertainty_m": event.location.uncertainty_m if event.location else None,
                       "raw_data": event.raw_data})
    return result


def setup() -> None:
    register_source("example_observations", kind="events")(fetch_observations)
