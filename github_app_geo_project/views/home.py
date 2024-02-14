"""Output view."""

import logging
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import configuration
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="home", renderer="github_app_geo_project:templates/home.html")  # type: ignore
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    applications = []
    for app in request.registry.settings["applications"].split():
        application = {
            "name": app,
            "github_app_id": request.registry.settings[f"application.{app}.github_app_id"],
            "github_app_private_key": request.registry.settings[f"application.{app}.github_app_private_key"],
            "github_app_url": request.registry.settings[f"application.{app}.github_app_url"],
            "title": request.registry.settings[f"application.{app}.title"],
            "description": request.registry.settings[f"application.{app}.description"],
            "modules": [],
        }
        for module_name in request.registry.settings[f"application.{app}.modules"].split():
            if module_name not in modules.MODULES:
                _LOGGER.error(f"Unknown module {module_name}")
                continue
            module = modules.MODULES[module_name]
            application["modules"].append(
                {
                    "name": module_name,
                    "title": module.title(),
                    "description": module.description(),
                    "configuration_url": module.configuration_url(),
                }
            )

        applications.append(application)

    return {
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "description": configuration.APPLICATION_CONFIGURATION["description"],
        "configuration_url": configuration.APPLICATION_CONFIGURATION["configuration_url"],
        "applications": applications,
    }
