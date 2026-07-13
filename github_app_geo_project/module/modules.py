"""Module registry."""

import logging
from importlib.metadata import entry_points
from typing import Any

from github_app_geo_project import module

# Available modules by name
MODULES: dict[str, module.Module[Any, Any, Any, Any]] = {}
_LOGGER = logging.getLogger(__name__)


for ep in entry_points(group="ghci.module"):
    if ep.name in MODULES:
        _LOGGER.warning("Duplicate module name: %s", ep.name)
    _LOGGER.info("Loading module: %s, from: %s", ep.name, ep.module)
    MODULES[ep.name] = ep.load()()
