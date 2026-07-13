"""Schema view."""

import json
import logging
from typing import Annotated, Any

import anyio
from fastapi import Depends

from github_app_geo_project.module import modules
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)


async def schema_view() -> dict[str, Any]:
    """Return the JSON schema."""
    module_names: set[str] = set()
    for app_config in settings.application_configs.values():
        module_names.update(app_config.modules)

    schema_path = anyio.Path(__file__).parent.parent / "project-schema.json"
    # get project-schema-content
    async with await schema_path.open(encoding="utf-8") as schema_file:
        schema: dict[str, Any] = json.loads(await schema_file.read())

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
                await modules.MODULES[module_name].get_json_schema(),
            ],
        }

    return schema


SchemaData = Annotated[dict[str, Any], Depends(schema_view)]
