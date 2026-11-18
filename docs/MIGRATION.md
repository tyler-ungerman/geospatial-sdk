# Migration to `gspace-sdk`

The distribution name for new installations is `gspace-sdk`, and the preferred
Python import namespace is `gspace`. The release candidate is not yet on PyPI;
from a repository checkout, install it with:

```bash
python -m pip install ./base
```

```python
from gspace.schemas import GeoPoint
from gspace.datasources import LayerRequest, SpatialQuery, query_osm_roads
```

During the 0.x compatibility period, matching `base.*` imports remain
available. Both namespaces resolve to the same module objects, so model types
and plugin contracts retain identity. Existing applications can migrate
imports incrementally; no data migration is required. The older distribution
name `geospatial-base-sdk` is not the package name for this release candidate.

The legacy `base.datasources.osm.fetch_roads(bounds, ...)` call and its
GeoJSON-shaped return value remain available. Its `coverage_complete` field
now reports false when tile results are incomplete or cannot be verified;
callers must not interpret failed or saturated tiles as complete coverage.
Use the typed `SpatialQuery` API with `require_complete=True` when complete
coverage is required, and handle `IncompleteCoverageError` explicitly.
