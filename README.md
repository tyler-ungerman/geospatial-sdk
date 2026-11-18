# Geospatial SDK

A Python framework for spatial data queries, coverage reporting, offline replay,
and independent plugins. The distribution is `gspace-sdk`; new projects should
import from `gspace`. Existing `base` imports remain supported during the 0.x
compatibility period.

```bash
python -m pip install -e ./base -e ./example-plugin
python -m pytest -q base/tests example-plugin/tests
geospatial plugin create my-map-plugin --path ./plugins
```

The editable install uses the local workspace. No PyPI release is available
yet.

The SDK provides a typed OSM query result with coverage, source records and
query diagnostics. An incomplete, unavailable or unverifiable tile is not
complete data; `require_complete=True` rejects it. A local GeoJSON adapter
can query a checksum-verified extract and replay a relocatable snapshot without
network access. Extract coverage is a claim supplied by the manifest author,
not something the checksum authenticates.

See the [SDK guide](base/README.md) for query examples, cache policies,
coordinate conventions, offline snapshots and plugin creation. The
[example plugin](example-plugin/README.md) demonstrates an independent
consumer. Live OSM and elevation services have rate limits and availability
constraints; retain source attribution and observe their data licences. Scores
produced by heuristic plugins are not calibrated probabilities.

The SDK and example plugin source are licensed under the MIT License; see
[LICENSE](LICENSE). This software licence does not apply to external map,
elevation or other provider data. Provider terms, attribution and data licences
remain in force for retrieved or locally supplied data; see
[licensing and attribution](docs/licensing.md).

The import and compatibility surface is described in
[docs/public-api.md](docs/public-api.md). The executable completeness and
deterministic replay example is [docs/quickstart_offline.py](docs/quickstart_offline.py).
