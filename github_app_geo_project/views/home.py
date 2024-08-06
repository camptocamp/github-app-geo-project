"""Output view."""

import logging
import os
from typing import Any, Literal, cast

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
from pyramid.view import view_config

from github_app_geo_project import configuration, module
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


def _gt_access(
    access_1: Literal["read", "write", "admin"], access_2: Literal["read", "write", "admin"]
) -> bool:
    access_number = {"read": 1, "write": 2, "admin": 3}
    return access_number[access_1] > access_number[access_2]


@view_config(route_name="home", renderer="github_app_geo_project.templates:home.html")  # type: ignore
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    repository = os.environ["C2C_AUTH_GITHUB_REPOSITORY"]
    user_permission = request.has_permission(
        repository,
        {"github_repository": repository, "github_access_type": "admin"},
    )
    admin = isinstance(user_permission, pyramid.security.Allowed)

    applications = []
    for app in request.registry.settings["applications"].split():
        application = {
            "name": app,
            "github_app_url": request.registry.settings.get(f"application.{app}.github_app_url"),
            "github_app_admin_url": (
                request.registry.settings.get(f"application.{app}.github_app_admin_url") if admin else None
            ),
            "title": request.registry.settings.get(f"application.{app}.title"),
            "description": request.registry.settings.get(f"application.{app}.description"),
            "modules": [],
            "repository_permissions": [],
            "organization_permissions": [],
            "account_permissions": [],
            "subscribe_to_events": [],
            "errors": [],
        }

        permissions: module.Permissions = {
            "contents": "read",
            # Impossible to remove this permission on GitHub, so we don't check it
            "metadata": "read",
            # Used to create and update the github checks
            "checks": "write",
        }
        events = set()

        for module_name in request.registry.settings[f"application.{app}.modules"].split():
            if module_name not in modules.MODULES:
                _LOGGER.error("Unknown module %s", module_name)
                continue
            module_instance = modules.MODULES[module_name]
            application["modules"].append(
                {
                    "name": module_name,
                    "title": module_instance.title(),
                    "description": module_instance.description(),
                    "documentation_url": module_instance.documentation_url(),
                    "has_transversal_dashboard": module_instance.has_transversal_dashboard() and admin,
                }
            )
            if module_instance.required_issue_dashboard():
                permissions["issues"] = "write"
                events.add("issues")
            module_permissions = module_instance.get_github_application_permissions()
            events.update(module_permissions.events)
            for name, access in module_permissions.permissions.items():
                if name not in permissions or _gt_access(access, permissions[name]):  # type: ignore[arg-type,literal-required]
                    permissions[name] = access  # type: ignore[literal-required]

        if admin:
            try:
                if "TEST_APPLICATION" not in os.environ:
                    github = (
                        configuration.get_github_application(request.registry.settings, app)
                        if admin
                        else None
                    )

                    github_events = set(github.integration.get_app().events)
                    # test that all events are in github_events
                    if not events.issubset(github_events):
                        application["errors"].append(
                            f"Missing events ({', '.join(events - github_events)}) "
                            f"in the GitHub application, please add them in the "
                            "GitHub configuration interface."
                        )
                        _LOGGER.error(
                            "Missing events (%s) in the GitHub application '%s', please add them in the "
                            "GitHub configuration interface.",
                            ", ".join(events - github_events),
                            app,
                        )
                        _LOGGER.info("Current events:\n%s", "\n".join(github_events))
                    if not github_events.issubset(events):
                        _LOGGER.error(
                            "The GitHub application '%s' has more events (%s) than required, please remove "
                            "them in the GitHub configuration interface.",
                            app,
                            ", ".join(github_events - events),
                        )

                    github_permissions = cast(module.Permissions, github.integration.get_app().permissions)
                    # Test that all permissions are in github_permissions
                    for permission, access in permissions.items():
                        if permission not in github_permissions or _gt_access(
                            access, github_permissions[permission]  # type: ignore[arg-type,literal-required]
                        ):
                            application["errors"].append(
                                f"Missing permission ({permission}={access}) in the GitHub application, "
                                "please add it in the GitHub configuration interface."
                            )
                            _LOGGER.error(
                                "Missing permission (%s=%s) in the GitHub application (%s) "
                                "please add it in the GitHub configuration interface.",
                                permission,
                                access,
                                app,
                            )
                            _LOGGER.info(
                                "Current permissions:\n%s",
                                "\n".join([f"{k}={v}" for k, v in github_permissions.items()]),
                            )
                        elif _gt_access(
                            github_permissions[permission], access  # type: ignore[arg-type,literal-required]
                        ):
                            _LOGGER.error(
                                "The GitHub application '%s' has more permission (%s=%s) than required, "
                                "please remove it in the GitHub configuration interface.",
                                app,
                                permission,
                                github_permissions[permission],  # type: ignore[literal-required]
                            )
                    for permission in github_permissions:
                        if permission not in permissions:
                            _LOGGER.error(
                                "Unnecessary permission (%s) in the GitHub application '%s', please remove it in the "
                                "GitHub configuration interface.",
                                permission,
                                app,
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
