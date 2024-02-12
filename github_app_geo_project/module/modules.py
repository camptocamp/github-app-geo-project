"""
Module registry.
"""

from typing import Any

from github_app_geo_project import module

# Available modules by name
MODULES: dict[str, module.Module[Any]] = {}
