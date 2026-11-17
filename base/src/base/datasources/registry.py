"""Registry for bundled and independently installed source integrations.

Plugins register on import via @register_source, then expose a no-arg setup
function through the `geospatial.sources` entry point group.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Callable, Literal, Mapping

from .http import FetchError, JsonRecord, Transport

logger = logging.getLogger(__name__)
SourceKind = Literal["events", "entities"]
FetchFunction = Callable[..., list[JsonRecord]]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    kind: SourceKind
    fetch: FetchFunction
    description: str = ""


@dataclass
class FetchReport:
    records: dict[str, list[JsonRecord]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def flatten(self, kind: SourceKind | None = None) -> list[JsonRecord]:
        return [record for name, rows in self.records.items()
                if kind is None or get_source(name).kind == kind for record in rows]


_REGISTRY: dict[str, SourceSpec] = {}
_LOADED: set[str] = set()


def register_source(name: str, *, kind: SourceKind) -> Callable[[FetchFunction], FetchFunction]:
    """Register one fetch function; accidental duplicate names are rejected."""
    if not name.strip() or kind not in ("events", "entities"):
        raise ValueError("A nonblank source name and kind events/entities are required")

    def decorator(fn: FetchFunction) -> FetchFunction:
        existing = _REGISTRY.get(name)
        if existing is not None and existing.fetch is not fn:
            raise ValueError(f"Source '{name}' is already registered")
        description = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        _REGISTRY[name] = SourceSpec(name, kind, fn, description)
        return fn
    return decorator


def load_plugin_sources(*, strict: bool = False) -> dict[str, str]:
    """Load installed third-party `geospatial.sources` setup entry points once.

    Errors are returned by entry-point name instead of silently suppressing them.
    Passing strict=True raises on first failure, useful in CI.
    """
    errors: dict[str, str] = {}
    for ep in entry_points(group="geospatial.sources"):
        identity = f"{ep.name}:{ep.value}"
        if identity in _LOADED:
            continue
        try:
            setup = ep.load()
            if not callable(setup):
                raise TypeError("entry point must resolve to a no-arg setup callable")
            setup()
            _LOADED.add(identity)
        except Exception as exc:
            if strict:
                raise
            errors[ep.name] = f"{type(exc).__name__}: {exc}"
            logger.warning("Unable to register source plugin %s: %s", ep.name, errors[ep.name])
    return errors


def get_source(name: str) -> SourceSpec:
    if name not in _REGISTRY:
        load_plugin_sources()
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown source '{name}'. Available: {sorted(_REGISTRY)}") from None


def list_sources(kind: SourceKind | None = None) -> list[SourceSpec]:
    load_plugin_sources()
    return [s for s in _REGISTRY.values() if kind is None or s.kind == kind]


def fetch_source(name: str, *, client: Transport | None = None, **options: Any) -> list[JsonRecord]:
    return get_source(name).fetch(client=client, **options)


def fetch_all(names: list[str] | None = None, *, kind: SourceKind | None = None,
              client: Transport | None = None, options: Mapping[str, Mapping[str, Any]] | None = None) -> FetchReport:
    """Collect independently; a FetchError never blocks other sources."""
    specs = [get_source(n) for n in names] if names is not None else list_sources(kind)
    if kind is not None:
        specs = [s for s in specs if s.kind == kind]
    report = FetchReport()
    for s in specs:
        try:
            report.records[s.name] = s.fetch(client=client, **(options or {}).get(s.name, {}))
        except FetchError as exc:
            logger.warning("Source %s failed: %s", s.name, exc)
            report.errors[s.name] = str(exc)
    return report
