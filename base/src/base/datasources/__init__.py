"""Pluggable source registry, common HTTP transport, and built-in collectors."""

from .http import (FetchError, SourceUnavailable, CircuitOpen, HttpClient, HttpResponse,
                   Transport, CachedTransport, RetryingTransport, JsonRecord)
from .registry import (
    SourceSpec, FetchReport, register_source, fetch_source, fetch_all, get_source,
    list_sources, load_plugin_sources,
)
from . import osint, osm, dem
from .query import (CoverageReport, IncompleteCoverageError, LayerRequest, LayerResult,
                    QueryDiagnostics, SourceRecord, SpatialQuery, query_osm_roads)

__all__ = ["FetchError", "SourceUnavailable", "CircuitOpen", "HttpClient", "HttpResponse", "Transport", "CachedTransport", "RetryingTransport", "JsonRecord",
           "SourceSpec", "FetchReport", "register_source", "fetch_source", "fetch_all", "get_source",
           "list_sources", "load_plugin_sources", "osint", "osm", "dem"]
__all__ += ["CoverageReport", "IncompleteCoverageError", "LayerRequest", "LayerResult",
            "QueryDiagnostics", "SourceRecord", "SpatialQuery", "query_osm_roads"]
