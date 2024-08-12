"""Output view."""

import json
import logging
import os.path
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
from pyramid.view import view_config

from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="schema", renderer="json")  # type: ignore
def schema_view(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    module_names = set()
    for app in request.registry.settings["applications"].split():
        module_names.update(request.registry.settings[f"application.{app}.modules"].split())

    # get project-schema-content
    with open(
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "project-schema.json"), encoding="utf-8"
    ) as schema_file:
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
                modules.MODULES[module_name].get_json_schema(),
            ],
        }

    return schema
