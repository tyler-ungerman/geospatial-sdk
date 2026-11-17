# Example bearing matcher plugin

Install alongside core from `geospatial/`:

```bash
python -m pip install -e ./base -e ./example-plugin
python -c "from base.plugins import list_plugins; print([p.name for p in list_plugins()])"
```

The `geospatial.modules` entry point registers `bearing_matcher` automatically, and
`geospatial.sources` registers `example_observations` without editing `base/`.

```python
from base.plugins import run_plugin
from base.schemas import GeoPoint

out = run_plugin("bearing_matcher", [GeoPoint(lat=56.34, lng=-2.80)],
                 GeoPoint(lat=56.35, lng=-2.80), observed_bearing_deg=0)
print(out[0].rank_score, out[0].confidence)  # 1.0, None (not calibrated)
```
