"""Output view."""

import logging
import os
from typing import Any

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
            "description": request.registry.settings[f"application.{app}.description"],
            "modules": [],
            "repository_permissions": [],
            "organization_permissions": [],
            "account_permissions": [],
            "subscribe_to_events": [],
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
                    "description": module.description(),
                    "documentation_url": module.documentation_url(),
                }
            )
            repository = os.environ["C2C_AUTH_GITHUB_REPOSITORY"]
            permission = request.has_permission(
                repository,
                {"github_repository": repository, "github_access_type": "admin"},
            )
            if isinstance(permission, pyramid.security.Allowed):
                permissions = module.get_github_application_permissions()
                application["repository_permissions"].extend(permissions["repository_permissions"])
                application["organization_permissions"].extend(permissions["organization_permissions"])
                application["account_permissions"].extend(permissions["account_permissions"])
                application["subscribe_to_events"].extend(permissions["subscribe_to_events"])

        applications.append(application)

    return {
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "description": configuration.APPLICATION_CONFIGURATION["description"],
        "documentation_url": configuration.APPLICATION_CONFIGURATION["documentation-url"],
        "profiles": configuration.APPLICATION_CONFIGURATION["profiles"],
        "applications": applications,
    }
