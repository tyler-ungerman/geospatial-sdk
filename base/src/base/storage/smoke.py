"""Non-destructive object-store smoke check with automatic cleanup and readback."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from base.datasources import Transport
from .backends import JSONStore, RunScopedStore
from .preprocessing import run_pipeline


def smoke_sources(store: JSONStore, names: list[str], *,
                  client: Transport | None = None,
                  options: Mapping[str, Mapping[str, Any]] | None = None,
                  now: datetime | None = None) -> dict:
    """Fetch selected sources, write temporary objects, read/inspect, cleanup on every exit.

    Does not reuse production keys, even for the final summary. Returns counts
    and sampled object data, but NOT a guarantee that remote APIs are up.
    """
    defaults: dict[str, dict[str, Any]] = {
        "usgs": {"limit": 5}, "firms": {"limit": 5},
        "gdacs": {"limit": 5, "lookback_days": 1}, "opensky": {"limit": 5},
        "gdelt": {"limit": 5}, "google_news": {"per_topic": 1, "inter_feed_delay": .1},
    }
    merged_options = {name: {**defaults.get(name, {}), **(options or {}).get(name, {})}
                      for name in names}
    with RunScopedStore(store) as scoped:
        summary = run_pipeline(names, options=merged_options, client=client, store=scoped, now=now)
        keys = scoped.list_keys()
        scoped.verify_readback()
        inspection = {}
        samples = {}
        for key in keys:
            value = scoped.read_json(key, default=None)
            if value is None:
                raise RuntimeError(f"Smoke read-back failed for {key}")
            samples[key] = value[:3] if isinstance(value, list) else value
            inspection[key] = {"kind": type(value).__name__,
                               "count": len(value) if isinstance(value, (list, dict)) else None}
        if not keys or not any(k.endswith("runs/latest.json") for k in keys):
            raise RuntimeError("Smoke run did not write its summary")
        if scoped.read_json("geospatial/runs/latest.json") != summary:
            raise RuntimeError("Smoke summary read-back mismatch")
        return {"summary": summary, "run_prefix": scoped.prefix,
                "inspected": inspection, "samples": samples,
                "cleanup": "attempted on context exit; check remote store after network failures"}
