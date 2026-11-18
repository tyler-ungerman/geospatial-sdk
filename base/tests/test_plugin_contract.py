import pytest
from pydantic import BaseModel, Field
from concurrent.futures import ThreadPoolExecutor

from base.plugins import (PluginSpec, list_plugins, plugin_discovery_report,
                          register_plugin, run_plugin, run_plugin_spec)
from base.schemas import BoundingBox


class ExtentReport(BaseModel):
    width_degrees: float = Field(gt=0)


def test_typed_plugin_validates_pydantic_input_and_output():
    def report_extent(region: BoundingBox) -> dict:
        return {"width_degrees": region.max_lon - region.min_lon}

    register_plugin("test_extent_report", "Typed extent report", api_version="1.2.0",
        input_type=BoundingBox, output_type=ExtentReport, resource_needs=("region",))(report_extent)
    result = run_plugin("test_extent_report", {
        "min_lat": 0, "min_lon": 1, "max_lat": 1, "max_lon": 3,
    })
    assert isinstance(result, ExtentReport)
    assert result.width_degrees == 2


def test_typed_plugin_rejects_invalid_input_output_and_version():
    def report_extent(region: BoundingBox):
        return {"width_degrees": 0}

    register_plugin("test_invalid_extent_report", api_version="0.1.0",
        input_type=BoundingBox, output_type=ExtentReport)(report_extent)
    with pytest.raises(TypeError, match="input"):
        run_plugin("test_invalid_extent_report", {"min_lat": 0})
    with pytest.raises(TypeError, match="output"):
        run_plugin("test_invalid_extent_report", BoundingBox(min_lat=0, min_lon=0, max_lat=1, max_lon=1))
    with pytest.raises(ValueError, match="MAJOR.MINOR.PATCH"):
        register_plugin("test_bad_version", api_version="latest")


def test_explicit_specs_run_concurrently_without_global_registration():
    specs = [
        PluginSpec("stateless_width", "", lambda box: {"width_degrees": box.max_lon - box.min_lon},
                   api_version="1.0.0", input_type=BoundingBox, output_type=ExtentReport),
        PluginSpec("stateless_height", "", lambda box: {"width_degrees": box.max_lat - box.min_lat},
                   api_version="1.0.0", input_type=BoundingBox, output_type=ExtentReport),
    ]
    region = {"min_lat": 10, "min_lon": 20, "max_lat": 13, "max_lon": 25}
    with ThreadPoolExecutor(max_workers=2) as pool:
        width, height = list(pool.map(lambda spec: run_plugin_spec(spec, region), specs))
    assert width.width_degrees == 5
    assert height.width_degrees == 3
    assert "stateless_width" not in [spec.name for spec in list_plugins()]
    assert "stateless_height" not in [spec.name for spec in list_plugins()]


def test_plugin_discovery_report_exposes_setup_errors_without_changing_list_api(monkeypatch):
    import base.plugins as plugin_module

    class BrokenEntryPoint:
        name = "broken_consumer"
        value = "broken:setup"

        @staticmethod
        def load():
            raise ImportError("missing optional dependency")

    monkeypatch.setattr(plugin_module, "entry_points", lambda **kwargs: [BrokenEntryPoint()])
    report = plugin_discovery_report()
    assert isinstance(report.plugins, tuple)
    assert report.errors == {"broken_consumer": "ImportError: missing optional dependency"}
    assert isinstance(list_plugins(), list)  # compatibility: failures remain non-fatal
