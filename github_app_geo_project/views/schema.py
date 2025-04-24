"""Output view."""

import json
import logging
from pathlib import Path
from typing import Any

import pyramid.request
from pyramid.view import view_config

from github_app_geo_project.module import modules
from github_app_geo_project.views import get_event_loop

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="schema", renderer="json")  # type: ignore[misc]
def schema_view(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    module_names = set()
    for app in request.registry.settings["applications"].split():
        module_names.update(request.registry.settings[f"application.{app}.modules"].split())

    # get project-schema-content
    schema_path = Path(__file__).parent.parent / "project-schema.json"
    with schema_path.open(encoding="utf-8") as schema_file:
        schema: dict[str, Any] = json.loads(schema_file.read())

    del schema["properties"]["module-configuration"]
    del schema["properties"]["example"]

    for module_name in module_names:
        if module_name not in modules.MODULES:
            _LOGGER.error("Unknown module %s", module_name)
            continue
        schema["properties"][module_name] = {
            "type": "object",
            "title": modules.MODULES[module_name].title(),
            "description": modules.MODULES[module_name].description(),
            "allOf": [
                {"$ref": "#/$defs/module-configuration"},
                get_event_loop().run_until_complete(modules.MODULES[module_name].get_json_schema()),
            ],
        }

    return schema
