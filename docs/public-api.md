# Public API and compatibility

The alpha distribution is `gspace-sdk`; the preferred import namespace is
`gspace`. Existing `base` imports remain supported for the 0.x compatibility
period and both paths resolve to the same module objects. Callers should pin
the package version they test. This document freezes the implemented surface
for the initial public alpha; it does not promise that every internal module is
stable.

## Supported entry points

The following imports remain supported:

- New projects may use `gspace.schemas`, `gspace.datasources`, `gspace.geo_math`,
  `gspace.stats`, `gspace.reporting`, `gspace.storage`, and `gspace.plugins`.
- Existing projects may continue using matching `base.*` paths. Classes
  imported through the two namespaces are identical objects, not wrappers.

- `base.schemas` / `gspace.schemas`: `GeoPoint`, `BoundingBox`, `EvidenceItem`, `GeoCandidate`,
  `GeoEvent`, `GeoEntity`, and `ElevationGrid`.
- `base.datasources` / `gspace.datasources`: source registration and fetching (`register_source`,
  `fetch_source`, `fetch_all`, `list_sources`), HTTP types and transports,
  `osm`, `dem`, plus the typed query API below.
- `base.geo_math`, `base.stats`, `base.reporting`, `base.storage`, and
  `base.plugins` retain their package paths and current function names; matching
  `gspace` paths are available for new code.
- `geospatial-sources` remains installed. The `geospatial` executable adds
  `geospatial plugin create` and does not replace it.
- Plugin entry-point groups remain `geospatial.modules` and
  `geospatial.sources`. Existing setup hooks and the default
  `list[GeoCandidate]` plugin result validation continue to work.

`gspace.__version__` and `base.__version__` report the same release version.
Neither root module re-exports every symbol; import models from
`gspace.schemas` (or `base.schemas`), and typed query types from
`gspace.datasources` or `gspace.datasources.query`.

## Completeness-aware query API

```python
from gspace.datasources import (
    IncompleteCoverageError,
    LayerRequest,
    SpatialQuery,
    query_osm_roads,
)
from gspace.schemas import BoundingBox

query = SpatialQuery(
    region=BoundingBox(min_lat=51.0, min_lon=-1.0, max_lat=51.1, max_lon=-0.9),
    layers=[LayerRequest(name="osm.roads", filters={"highway_types": ["residential"]})],
    offline=True,
    require_complete=True,
    cache_policy="read_only",
)
try:
    result = query_osm_roads(query)
except IncompleteCoverageError as error:
    print(error.coverage.status, error.coverage.failed_tile_ids)
else:
    print(result.coverage.status, len(result.data["features"]))
    print(result.provenance[0].attribution)
```

`SpatialQuery` currently accepts a WGS84 `BoundingBox`, one or more
`LayerRequest` values, `offline`, `require_complete`, cache policy
(`read_write`, `read_only`, `refresh`), and `max_requests`. The OSM adapter
currently implements `osm.roads`. `LayerResult` separates GeoJSON data from
`CoverageReport`, source records and query diagnostics. An empty, verified tile
is complete; saturated, failed, malformed, or unverifiable cache tiles are
partial/unknown and cannot satisfy `require_complete=True`.

`max_requests` counts calls dispatched through the adapter to its `Transport`.
A custom transport that retries internally must apply its own attempt limit.
`read_only` implies offline reads. `refresh` cannot be combined with
`offline=True`.

The legacy `base.datasources.osm.fetch_roads(bounds, ...)` function and its
GeoJSON `dict` result remain available. Its `coverage_complete` field now
fails closed for unverified or incomplete tile results; this is a deliberate
semantic correction for callers that used the field. Existing imports and the
result's `type` and `features` fields are retained.

## Local extract and deterministic replay

Use `query_local_osm_roads` and `snapshot_local_osm_extract` from
`gspace.datasources.local_osm`. The adapter reads GeoJSON or the generated ZIP
snapshot without network access. A manifest checksum ties the extract bytes
to source metadata, while coverage and licence details remain publisher
claims. Unsupported coordinate reference systems fail closed. The complete
offline flow is executable as `python docs/quickstart_offline.py` from the
repository root; it generates a synthetic source, requires complete coverage,
creates a snapshot, replays it, and checks that normalized data and provenance
match.

See [migration notes](MIGRATION.md) before upgrading applications that relied
on the legacy completeness flag.
