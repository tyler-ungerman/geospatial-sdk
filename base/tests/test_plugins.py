import pytest
pytest.importorskip("bearing_matcher")

from base.datasources import fetch_source, list_sources
from base.plugins import list_plugins, run_plugin
from base.schemas import GeoPoint


def test_independently_installed_domain_and_source_plugins():
    import bearing_matcher
    assert "bearing_matcher" in [p.name for p in list_plugins()]
    assert "example_observations" in [s.name for s in list_sources()]
    result = run_plugin("bearing_matcher", [GeoPoint(lat=56.34, lng=-2.8)],
                        GeoPoint(lat=56.35, lng=-2.8), observed_bearing_deg=0)
    assert result[0].rank_score == 1
    assert result[0].confidence is None
    observations = [{"id": "event-1", "title": "Observed", "source": "own-sensors",
                     "category": "test", "severity": "low",
                     "location": {"lat": 0, "lng": 0, "uncertainty_m": 100}}]
    assert fetch_source("example_observations", observations=observations)[0]["lat"] == 0
