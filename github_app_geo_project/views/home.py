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


def _compare_access(access_1: str, access_2: str) -> bool:
    access_number = {"read": 1, "write": 2, "admin": 3}
    return access_number[access_1] > access_number[access_2]


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
            "errors": [],
        }

        permissions: dict[str, str] = {}
        events = set()

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
            if module.required_issue_dashboard():
                permissions["issues"] = "write"
            module_permissions = module.get_github_application_permissions()
            events.update(module_permissions.events)
            for name, access in module_permissions.permissions.items():
                if name not in permissions or _compare_access(access, permissions[name]):
                    permissions[name] = access

        repository = os.environ["C2C_AUTH_GITHUB_REPOSITORY"]
        user_permission = request.has_permission(
            repository,
            {"github_repository": repository, "github_access_type": "admin"},
        )
        admin = isinstance(user_permission, pyramid.security.Allowed)
        if admin:
            try:
                github = configuration.get_github_objects(request.registry.settings, app) if admin else None

                if "TEST_APPLICATION" not in os.environ:
                    github_events = set(github.integration.get_app().events)
                    # test that all events are in github_events
                    if not events.issubset(github_events):
                        application["errors"].append(
                            "Missing events (%s) in the GitHub application, please add them in the GitHub configuration interface."
                            % ", ".join(events - github_events)
                        )
                        _LOGGER.error(application["errors"][-1])
                        _LOGGER.info("Current events:\n%s", "\n".join(github_events))

                    github_permissions = github.integration.get_app().permissions
                    # test that all permissions are in github_permissions
                    for permission, access in permissions.items():
                        if permission not in github_permissions or not _compare_access(
                            access, github_permissions[permission]
                        ):
                            application["errors"].append(
                                "Missing permission (%s=%s) in the GitHub application, please add it in the GitHub configuration interface."
                                % (permission, access)
                            )
                            _LOGGER.error(application["errors"][-1])
                            _LOGGER.info(
                                "Current permissions:\n%s",
                                "\n".join([f"{k}={v}" for k, v in github_permissions.items()]),
                            )
                else:
                    application["errors"].append("TEST_APPLICATION is set, no GitHub API call is made.")
                    _LOGGER.error(application["errors"][-1])
            except Exception as exception:  # pylint: disable=broad-exception-caught
                application["errors"].append(str(exception))
                _LOGGER.error(application["errors"][-1], exception)

        applications.append(application)

    return {
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "description": configuration.APPLICATION_CONFIGURATION["description"],
        "documentation_url": configuration.APPLICATION_CONFIGURATION["documentation-url"],
        "profiles": configuration.APPLICATION_CONFIGURATION["profiles"],
        "applications": applications,
    }
