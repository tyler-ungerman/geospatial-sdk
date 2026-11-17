"""Pluggable stores. Local JSON has no external dependencies; R2 is opt-in."""
from .backends import JSONStore, LocalJSONStore, R2Store, RunScopedStore
from .smoke import smoke_sources
from .preprocessing import (
    run_pipeline, normalize_event, normalize_entity, normalize_events, normalize_entities,
    merge_by_id, event_id,
)

__all__ = ["JSONStore", "LocalJSONStore", "R2Store", "RunScopedStore", "run_pipeline",
           "normalize_event", "normalize_entity", "normalize_events", "normalize_entities",
           "merge_by_id", "event_id", "smoke_sources"]
