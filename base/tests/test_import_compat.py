import base.schemas
import base.datasources.osm
import gspace.schemas
import gspace.datasources.osm
import importlib
import pkgutil
import sys


def test_gspace_and_base_import_paths_share_public_module_objects():
    assert gspace.schemas is base.schemas
    assert gspace.datasources.osm is base.datasources.osm
    assert gspace.schemas.GeoPoint is base.schemas.GeoPoint
    assert gspace.datasources.SpatialQuery is base.datasources.SpatialQuery


def test_gspace_exposes_same_sdk_version():
    import base
    import gspace

    assert gspace.__version__ == base.__version__


def test_gspace_package_discovery_uses_alias_modules_without_duplicates():
    discovered = {entry.name for entry in pkgutil.iter_modules(gspace.__path__)}
    assert {"schemas", "datasources", "geo_math", "plugins"} <= discovered

    aliased = importlib.import_module("gspace.geo_math")
    original = importlib.import_module("base.geo_math")

    assert aliased is original
    assert sys.modules["gspace.geo_math"] is sys.modules["base.geo_math"]
