"""Preferred public import namespace for the Geospatial SDK.

The implementation remains in :mod:`base` during the 0.x compatibility
period. This import bridge makes ``gspace.*`` and ``base.*`` resolve to the
same module objects, so Pydantic classes and plugin contracts keep identity.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import sys
from types import ModuleType

import base as _base

__version__ = _base.__version__
# Keep normal package introspection (for example pkgutil.iter_modules) useful.
# The alias finder below still redirects gspace.* imports to base.* so modules
# discovered at this shared path are never loaded a second time by source path.
__path__ = _base.__path__


class _BaseAliasLoader(importlib.abc.Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module: ModuleType) -> None:
        # create_module returned the already loaded base module. Do not execute
        # source twice under a second name.
        return None


class _BaseAliasFinder(importlib.abc.MetaPathFinder):
    _prefix = "gspace."

    def find_spec(self, fullname: str, path=None, target=None):
        if not fullname.startswith(self._prefix):
            return None
        base_name = "base." + fullname[len(self._prefix):]
        module = importlib.import_module(base_name)
        return importlib.machinery.ModuleSpec(
            fullname,
            _BaseAliasLoader(base_name),
            is_package=hasattr(module, "__path__"),
        )


if not any(isinstance(finder, _BaseAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _BaseAliasFinder())
