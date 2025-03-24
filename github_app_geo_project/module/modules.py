"""Module registry."""

import logging
from typing import Any

import pkg_resources

from github_app_geo_project import module

# Available modules by name
MODULES: dict[str, module.Module[Any, Any, Any, Any]] = {}
_LOGGER = logging.getLogger(__name__)


for ep in pkg_resources.iter_entry_points(group="ghci.module"):
    if ep.name in MODULES:
        _LOGGER.warning("Duplicate module name: %s", ep.name)
    _LOGGER.info("Loading module: %s, from: %s", ep.name, ep.module_name)
    MODULES[ep.name] = ep.load()()
