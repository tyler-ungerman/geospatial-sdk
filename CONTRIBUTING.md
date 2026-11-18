# Contributing

Use Python 3.10 or newer. Install and test the public packages from the
repository root:

```bash
python -m pip install -e ./base -e ./example-plugin pytest
python -m pytest -q base/tests example-plugin/tests
```

Prefer deterministic tests with local fixtures and injected transports. Tests
must distinguish an empty successful source response from unavailable or
incomplete coverage. Describe any live-service requirements and retain source
attribution. Keep existing `base` imports and plugin entry points compatible
unless a breaking change is explicitly planned.

Never commit credentials, private datasets, cached responses or model weights.
Before proposing changes, inspect `git status` and the staged file list. Do not
include private projects in public changes. CI runs the public SDK and example
plugin tests on Python 3.10–3.13.
