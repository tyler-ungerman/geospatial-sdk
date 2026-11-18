# Geospatial Base SDK

An installable **shared Python package**, distributed as `gspace-sdk`. New
projects should import `gspace`; the existing `base` namespace remains
supported during the 0.x compatibility period.
Data collection, common contracts, geometry, statistics, storage, and reporting live here;
application-specific CV/ML/analytics logic belongs in plugins.

## Installation

From the `geospatial/` workspace:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e './base[dev]'
pip install -e ./example-plugin
pytest base/tests
```

For R2 use `pip install -e './base[r2]'`; interactive Folium maps use `[map]`.
Python 3.10+; `pydantic` v2 is the only runtime dependency.

## Package map

```text
base/src/base/
  schemas/               GeoPoint, BoundingBox, EvidenceItem, GeoCandidate, GeoEvent, GeoEntity, ElevationGrid
  datasources/           HTTP transport, TTL cache, source registration, OSM roads, SRTM DEM
    osint/               USGS, FIRMS, GDACS, OpenSky, GDELT, Google News
  geo_math/              haversine, bearings, radius-bbox, CRS, LOS, horizon, solar azimuth
  stats/                 DBSCAN-style clustering, evidence ranking, Bayes updating, evaluation
  modeling/              NDVI, NDMI, percentile helpers
  reporting/             GeoJSON, JSON, optional Folium HTML
  storage/               local JSON, lazy R2, temporary run cleanup, preprocess pipeline
  plugins.py             Independent domain-plugin registration and discovery
```

## Quickstart, offline

```python
from gspace.schemas import GeoPoint, BoundingBox, EvidenceItem, GeoCandidate
from gspace.geo_math import haversine_m, bbox_from_radius
from gspace.stats import aggregate_evidence
from gspace.reporting import export_geojson

origin = GeoPoint(lat=56.3398, lng=-2.7967, uncertainty_m=25)
area = bbox_from_radius(origin, 1000)
assert area.contains(origin)
c = GeoCandidate(id="sample", location=origin, evidence=[
    EvidenceItem(id="measurement-1", source="field-note", description="Nearby landmark", score=0.6)
])
ranked = aggregate_evidence([c])
export_geojson(ranked, "candidates.geojson")
assert ranked[0].confidence is None  # rank score is not calibrated probability
```

## Data ingestion

```python
from base.datasources import list_sources, fetch_all, CachedTransport, HttpClient
from base.storage import LocalJSONStore, run_pipeline

print([(s.name, s.kind) for s in list_sources()])
store = LocalJSONStore("./data")
# Live external fetch (opt-in) — only the chosen source is requested:
summary = run_pipeline(["usgs"], store=store)
```

The `base.storage.db` module retains the old `write_json/read_json/list_keys/...`
functions, but now connects to R2 only when first used. Set `R2_ACCOUNT_ID`,
`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET_NAME` in your **own app**
environment. Base intentionally does not include or auto-read any `.env` file.
The old pipeline layout under `geospatial/events/...`, `geospatial/entities/...`
remains supported by `run_pipeline`.

## Querying roads and elevations

```python
from base.schemas import BoundingBox
from base.datasources import osm, dem

bounds = BoundingBox(min_lat=56.33, min_lon=-2.81, max_lat=56.35, max_lon=-2.78)
roads = osm.fetch_roads(bounds, highway_types=["primary", "secondary"])
elevation = dem.fetch_patch(bounds, rows=4, cols=4)
```

For callers that need coverage and source lineage, use the typed OSM query
surface. A saturated response or an older cache entry without verification
metadata is reported as partial, while the existing `fetch_roads()` API
remains available:

```python
from base.datasources import LayerRequest, SpatialQuery, query_osm_roads

query = SpatialQuery(
    region=bounds,
    layers=[LayerRequest(name="osm.roads", filters={"highway_types": ["primary"]})],
    offline=True,
)
result = query_osm_roads(query)
print(result.coverage.status, len(result.data["features"]))
```

Set `require_complete=True` to raise `IncompleteCoverageError` with a
structured coverage report whenever any tile is missing, saturated, or
unverifiable. A successful empty tile remains complete. GeoJSON coordinates
use `[longitude, latitude]`; source attribution is included in provenance.
`max_requests` caps calls dispatched by the adapter through its supplied (or
default) `Transport`. A retry hidden inside a caller-provided transport counts
as one adapter call; that transport must enforce its own retry limit.

### Local OSM extracts and replay

`base.datasources.local_osm.query_local_osm_roads` reads GeoJSON locally and
never uses the network. Put a `roads.geojson.manifest.json` sidecar next to
the extract with a SHA-256 checksum, provider/source ID, declared coverage
bounds, a completeness claim, and any known licence/attribution. The adapter
reports `unknown` when these claims are absent, reports partial coverage when
the requested bounds exceed the declared extent, and rejects checksum
mismatches. The manifest is a trusted source declaration supplied by the
extract publisher: its checksum detects changed GeoJSON relative to that
manifest, but it does not authenticate the manifest or prove the publisher's
completeness claim. Coordinates must use GeoJSON's default WGS84 longitude/
latitude axes or an explicit OGC CRS84 declaration; other CRS declarations,
including EPSG:4326 where axis semantics are ambiguous, are rejected. Only
`LineString` and `MultiLineString` road geometries
are supported; unsupported or malformed relevant geometries make the full
requested region unknown within a partial result. Conflicting duplicate OSM IDs are retained with a
stable conflict suffix and make the result partial.

`snapshot_local_osm_extract(source, "roads.zip")` writes a deterministic,
relocatable snapshot containing the exact GeoJSON bytes and manifest.
`query_local_osm_roads(query, "roads.zip")` replays it without extraction or
network access and verifies the data checksum before returning results.
Uncompressed GeoJSON members are limited to 512 MiB and manifest members to
2 MiB to bound snapshot reads.

These two adapters use the public Overpass and OpenTopoData APIs. They have finite
request sizes/timeouts, no bulk download API, and no guaranteed uptime. Elevation
is SRTM90m (roughly ±60° latitude); missing cells return None. See source sites
for rate limits/licensing and OSM attribution requirements. For large-scale or
repeated geospatial analysis, ingest a licensed local extract or raster separately.

## Plugin contract

Each domain plugin is an independent distributable project. It imports `base`
without copying core code, then provides setup functions via Python package
entry points:

```toml
[project.entry-points."geospatial.modules"]
my_plugin = "my_plugin.registration:setup"
[project.entry-points."geospatial.sources"]
my_source = "my_plugin.source:setup"
```

```python
from base.plugins import register_plugin
from base.datasources import register_source

# Called via entry-point discovery:
def setup():
    register_plugin("my_plugin", "Description")(run)

# The plugin's run(input, **options) returns list[GeoCandidate].
# Source handlers have signature fetch(*, client=None, **kwargs) -> list[dict].
# Use a separate source setup callable if both entry-point groups are exposed.
```

The runner validates the output type; it does **not** imply calibrated
probabilities. Use `base.stats.evaluation` on held-out ground truth and only set
`GeoCandidate.confidence` when empirically justified. Location radii of `None`
mean **unknown**, not zero. Inferred news locations are flagged as such.

For isolated pipelines, construct a `PluginSpec` and call
`run_plugin_spec(spec, ...)`; this validates inputs and outputs without
registering the spec or discovering entry points. `plugin_discovery_report()`
returns the registered specs and entry-point setup errors. The existing
`list_plugins()` still returns `list[PluginSpec]` for compatibility.

To create an installable starter package, install the SDK and run the scaffold
command from the repository root:

```bash
python -m pip install -e ./base
geospatial plugin create my-map-plugin --path ./plugins
python -m pip install -e './plugins/my_map_plugin[dev]'
python -m pytest ./plugins/my_map_plugin/tests
python ./plugins/my_map_plugin/examples/run_pipeline.py
```

The generated package includes a `geospatial.modules` entry point, a typed
Pydantic input/output example, a test, an example pipeline, and its own
README. The scaffold refuses to overwrite an existing destination. The
existing `geospatial-sources` command remains available for source listing and
fetching.

## Testing without touching R2

```bash
pytest base/tests -q
geospatial-sources                     # lists sources, no live HTTP
geospatial-sources usgs                # live HTTP, read-only
geospatial-sources usgs --store local  # live HTTP, local JSON only
geospatial-sources usgs --store local --smoke  # limited fetch, inspect, auto-cleanup
geospatial-sources usgs --store r2 --smoke     # remote smoke; explicit R2 credentials needed
```

`RunScopedStore` supports R2 smoke tests with random prefixes and cleanup in
`finally`, but a process crash or remote deletion failure can leave temporary
objects behind. The smoke runner verifies every JSON read-back and returns
samples and counts; any live R2 test still needs credentials and explicit
permission. Never run tests against a production key prefix. See `docs/MIGRATION.md`.

### Operational caveats

The built-in OpenSky military/civilian label is a callsign heuristic, not an
identity verification. GDELT and Google News coordinate predictions are
AI-inferred only when explicitly enriched, and their uncertainty remains
unquantified until evaluation. Ranking scores are never called probabilities.

The original `base.zip` supplied for this refactor contained a `.env` with
credentials; it is **not** included in this distribution. Rotate credentials
if the original archive was distributed to unintended recipients.
