"""Output view."""

import logging
from typing import Any

import markdown
import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
from pyramid.view import view_config

from github_app_geo_project import configuration
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="home", renderer="github_app_geo_project.templates:home.html")  # type: ignore
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    applications = []
    for app in request.registry.settings["applications"].split():
        application = {
            "name": app,
            "github_app_url": request.registry.settings[f"application.{app}.github_app_url"],
            "title": request.registry.settings[f"application.{app}.title"],
            "description": markdown.markdown(request.registry.settings[f"application.{app}.description"]),
            "modules": [],
        }
        for module_name in request.registry.settings[f"application.{app}.modules"].split():
            if module_name not in modules.MODULES:
                _LOGGER.error("Unknown module %s", module_name)
                continue
            module = modules.MODULES[module_name]
            application["modules"].append(
                {
                    "name": module_name,
                    "title": module.title(),
                    "description": markdown.markdown(module.description()),
                    "documentation_url": module.documentation_url(),
                }
            )

        applications.append(application)

    return {
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "description": markdown.markdown(configuration.APPLICATION_CONFIGURATION["description"]),
        "documentation_url": configuration.APPLICATION_CONFIGURATION["documentation-url"],
        "applications": applications,
    }
