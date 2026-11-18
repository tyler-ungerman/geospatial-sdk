# Example bearing matcher plugin

Install the SDK and example plugin from the repository root:

```bash
python -m pip install -e ./base -e ./example-plugin
python -c "from gspace.plugins import list_plugins; print([p.name for p in list_plugins()])"
python -m pytest base/tests example-plugin/tests
```

The `geospatial.modules` entry point registers `bearing_matcher` automatically, and
`geospatial.sources` registers `example_observations` without editing `base/`.

```python
from gspace.plugins import run_plugin
from gspace.schemas import GeoPoint

out = run_plugin("bearing_matcher", [GeoPoint(lat=56.34, lng=-2.80)],
                 GeoPoint(lat=56.35, lng=-2.80), observed_bearing_deg=0)
print(out[0].rank_score, out[0].confidence)  # 1.0, None (not calibrated)
```

Plugins can also declare typed inputs and outputs. This example accepts the
SDK's `LayerResult` and returns a validated `RoadLayerSummary` model:

```python
from gspace.plugins import run_plugin
from bearing_matcher.plugin import RoadLayerSummary

summary = run_plugin("road_layer_summary", roads_layer_result)
assert isinstance(summary, RoadLayerSummary)
print(summary.coverage_status, summary.highway_counts)
```

Existing plugins that omit `output_type` keep the original
`list[GeoCandidate]` validation. The entry point group remains
`geospatial.modules`; no core registry edits are needed for installation.

Applications that do not want process-wide plugin registration can create a
`PluginSpec` directly and pass it to `run_plugin_spec`. `list_plugins()` keeps
its list return type; use `plugin_discovery_report().errors` to inspect failed
optional plugin setup hooks.

To create an independent plugin, package a no-argument `setup()` function as
a `geospatial.modules` entry point and register the callable there:

```toml
[project.entry-points."geospatial.modules"]
my_plugin = "my_plugin.registration:setup"
```

```python
from gspace.datasources.query import LayerResult
from gspace.plugins import register_plugin
from my_plugin.models import MySummaryModel

def summarize(layer):
    return {"feature_count": len(layer.data["features"])}

def setup():
    register_plugin("my_summary", "Summarize a spatial layer",
                    api_version="1.0.0", input_type=LayerResult,
                    output_type=MySummaryModel,
                    resource_needs=("osm.roads",))(summarize)
```

`input_type` and `output_type` accept runtime types. Pydantic model inputs are
validated from mappings, and Pydantic model outputs are validated and
returned as model instances. `resource_needs` documents dependencies; the SDK
does not fetch them automatically.

Plugins can also declare typed inputs and outputs. This example accepts the
SDK's `LayerResult` and returns a validated `RoadLayerSummary` model:

```python
from gspace.plugins import run_plugin
from bearing_matcher.plugin import RoadLayerSummary

summary = run_plugin("road_layer_summary", roads_layer_result)
assert isinstance(summary, RoadLayerSummary)
print(summary.coverage_status, summary.highway_counts)
```

Existing plugins that omit `output_type` keep the original
`list[GeoCandidate]` validation. The entry point group remains
`geospatial.modules`; no core registry edits are needed for installation.
