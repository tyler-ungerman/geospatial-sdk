"""Domain workflow metadata, independent of any data source registry."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import entry_points
import re
from typing import Any, Callable

from base.schemas import GeoCandidate


@dataclass(frozen=True)
class PluginSpec:
    name: str
    description: str
    run: Callable[..., Any]
    api_version: str = "0.1.0"
    input_type: type | tuple[type, ...] | None = None
    output_type: type | tuple[type, ...] | None = None
    resource_needs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("plugin name cannot be blank")
        if not callable(self.run):
            raise TypeError("plugin run must be callable")
        if re.fullmatch(r"\d+\.\d+\.\d+", self.api_version) is None:
            raise ValueError("plugin api_version must use MAJOR.MINOR.PATCH")
        for label, value in (("input_type", self.input_type), ("output_type", self.output_type)):
            if value is not None and not (
                isinstance(value, type) or
                isinstance(value, tuple) and value and all(isinstance(item, type) for item in value)
            ):
                raise TypeError(f"{label} must be a type or non-empty tuple of types")


@dataclass(frozen=True)
class PluginDiscoveryReport:
    plugins: tuple[PluginSpec, ...]
    errors: dict[str, str]


_PLUGINS: dict[str, PluginSpec] = {}
_LOADED: set[str] = set()


def register_plugin(name: str, description: str = "", *, api_version: str = "0.1.0",
                    input_type: type | tuple[type, ...] | None = None,
                    output_type: type | tuple[type, ...] | None = None,
                    resource_needs: tuple[str, ...] = ()) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a plugin while retaining the legacy candidate-list default.

    ``input_type`` and ``output_type`` are optional runtime contracts. A
    Pydantic ``BaseModel`` output type also validates and converts dictionaries
    returned by the plugin. Omitting ``output_type`` keeps the original
    ``list[GeoCandidate]`` contract.
    """
    if not name.strip():
        raise ValueError("plugin name cannot be blank")
    if re.fullmatch(r"\d+\.\d+\.\d+", api_version) is None:
        raise ValueError("plugin api_version must use MAJOR.MINOR.PATCH")
    for label, value in (("input_type", input_type), ("output_type", output_type)):
        if value is not None and not (
            isinstance(value, type) or
            isinstance(value, tuple) and value and all(isinstance(item, type) for item in value)
        ):
            raise TypeError(f"{label} must be a type or non-empty tuple of types")
    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if name in _PLUGINS and _PLUGINS[name].run is not fn:
            raise ValueError(f"Plugin {name!r} already registered")
        _PLUGINS[name] = PluginSpec(name=name, description=description, run=fn,
            api_version=api_version, input_type=input_type, output_type=output_type,
            resource_needs=tuple(resource_needs))
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
    """Return registered plugins, preserving the original list API."""
    return list(plugin_discovery_report().plugins)


def plugin_discovery_report(*, strict: bool = False) -> PluginDiscoveryReport:
    """Return registered plugins and any entry-point setup failures."""
    errors = discover_plugins(strict=strict)
    plugins = tuple(sorted(_PLUGINS.values(), key=lambda p: p.name))
    return PluginDiscoveryReport(plugins=plugins, errors=errors)


def run_plugin_spec(spec: PluginSpec, *args: Any, **kwargs: Any) -> Any:
    """Invoke one explicit plugin specification without registry discovery."""
    if not isinstance(spec, PluginSpec):
        raise TypeError("spec must be a PluginSpec")
    if spec.input_type is not None:
        if not args:
            raise TypeError(f"Plugin {spec.name!r} requires an input matching {spec.input_type}")
        first = args[0]
        if isinstance(spec.input_type, type) and hasattr(spec.input_type, "model_validate"):
            if not isinstance(first, spec.input_type):
                try:
                    first = spec.input_type.model_validate(first)
                except Exception as exc:
                    raise TypeError(f"Plugin {spec.name!r} input does not satisfy {spec.input_type.__name__}") from exc
                args = (first, *args[1:])
        elif not isinstance(first, spec.input_type):
            raise TypeError(f"Plugin {spec.name!r} input must match {spec.input_type}")
    output = spec.run(*args, **kwargs)
    if spec.output_type is None:
        if not isinstance(output, list) or any(not isinstance(c, GeoCandidate) for c in output):
            raise TypeError("Plugin run must return list[GeoCandidate] unless output_type is declared")
        return output
    if isinstance(spec.output_type, type) and hasattr(spec.output_type, "model_validate"):
        try:
            return spec.output_type.model_validate(output)
        except Exception as exc:
            raise TypeError(f"Plugin {spec.name!r} output does not satisfy {spec.output_type.__name__}") from exc
    if not isinstance(output, spec.output_type):
        raise TypeError(f"Plugin {spec.name!r} output must match {spec.output_type}")
    return output


def run_plugin(name: str, *args: Any, **kwargs: Any) -> Any:
    discover_plugins()
    if name not in _PLUGINS:
        raise KeyError(f"No registered plugin {name!r}")
    return run_plugin_spec(_PLUGINS[name], *args, **kwargs)
