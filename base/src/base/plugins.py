"""Domain workflow metadata, independent of any data source registry."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, Callable

from base.schemas import GeoCandidate


@dataclass(frozen=True)
class PluginSpec:
    name: str
    description: str
    run: Callable[..., list[GeoCandidate]]


_PLUGINS: dict[str, PluginSpec] = {}
_LOADED: set[str] = set()


def register_plugin(name: str, description: str = "") -> Callable[[Callable[..., list[GeoCandidate]]], Callable[..., list[GeoCandidate]]]:
    """Decorator for a plugin's run(input, **options) -> list[GeoCandidate]."""
    if not name.strip():
        raise ValueError("plugin name cannot be blank")
    def decorator(fn: Callable[..., list[GeoCandidate]]) -> Callable[..., list[GeoCandidate]]:
        if name in _PLUGINS and _PLUGINS[name].run is not fn:
            raise ValueError(f"Plugin {name!r} already registered")
        _PLUGINS[name] = PluginSpec(name=name, description=description, run=fn)
        return fn
    return decorator


def discover_plugins(*, strict: bool = False) -> dict[str, str]:
    """Load no-arg setup functions exposed through group `geospatial.modules`."""
    errors = {}
    for ep in entry_points(group="geospatial.modules"):
        key = f"{ep.name}:{ep.value}"
        if key in _LOADED:
            continue
        try:
            setup = ep.load()
            if not callable(setup):
                raise TypeError("plugin entry point must be callable")
            setup()
            _LOADED.add(key)
        except Exception as exc:
            if strict:
                raise
            errors[ep.name] = f"{type(exc).__name__}: {exc}"
    return errors


def list_plugins() -> list[PluginSpec]:
    discover_plugins()
    return sorted(_PLUGINS.values(), key=lambda p: p.name)


def run_plugin(name: str, *args: Any, **kwargs: Any) -> list[GeoCandidate]:
    discover_plugins()
    if name not in _PLUGINS:
        raise KeyError(f"No registered plugin {name!r}")
    output = _PLUGINS[name].run(*args, **kwargs)
    if not isinstance(output, list) or any(not isinstance(c, GeoCandidate) for c in output):
        raise TypeError("Plugin run must return list[GeoCandidate]")
    return output
