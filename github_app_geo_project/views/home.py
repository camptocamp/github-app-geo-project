"""Home view."""

import logging
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, Request

from github_app_geo_project import configuration, module
from github_app_geo_project.module import modules
from github_app_geo_project.security import User, get_user
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)


def _gt_access(
    access_1: Literal["read", "write", "admin"],
    access_2: Literal["read", "write", "admin"],
) -> bool:
    access_number = {"read": 1, "write": 2, "admin": 3}
    return access_number[access_1] > access_number[access_2]


async def home(
    request: Request,
    user: Annotated[User, Depends(get_user)],
) -> dict[str, Any]:
    """Render the home page."""
    admin = user.is_admin

    applications: list[dict[str, Any]] = []
    for app_name, app_config in settings.application_configs.items():
        application: dict[str, Any] = {
            "name": app_name,
            "github_app_url": app_config.github_app.url,
            "github_app_admin_url": app_config.github_app.admin_url if admin else None,
            "title": app_config.title,
            "description": app_config.description,
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
        events: set[str] = set()

        for module_name in app_config.modules:
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
                },
            )
            if module_instance.required_issue_dashboard():
                permissions["issues"] = "write"
                events.add("issues")
            module_permissions = module_instance.get_github_application_permissions()
            events.update(module_permissions.events)
            name: module.PermissionsKey
            access: Literal["read", "write"]
            for name, access in module_permissions.permissions.items():  # type: ignore[assignment]
                if name not in permissions or _gt_access(access, permissions[name]):
                    permissions[name] = access

        if admin:
            try:
                if not settings.test.app_name:
                    github_application = await configuration.get_github_application(app_name)

                    github_authenticated_response = (
                        await github_application.aio_github.rest.apps.async_get_authenticated()
                    )
                    github_authenticated = github_authenticated_response.parsed_data
                    assert github_authenticated is not None
                    github_events = set(github_authenticated.events)
                    # test that all events are in github_events
                    if not events.issubset(github_events):
                        application["errors"].append(
                            f"Missing events ({', '.join(events - github_events)}) "
                            f"in the GitHub application, please add them in the "
                            "GitHub configuration interface.",
                        )
                        _LOGGER.error(
                            "Missing events (%s) in the GitHub application '%s', please add them in the "
                            "GitHub configuration interface.",
                            ", ".join(events - github_events),
                            app_name,
                        )
                        _LOGGER.info("Current events:\n%s", "\n".join(github_events))
                    if not github_events.issubset(events):
                        _LOGGER.error(
                            "The GitHub application '%s' has more events (%s) than required, please remove "
                            "them in the GitHub configuration interface.",
                            app_name,
                            ", ".join(github_events - events),
                        )

                    github_permissions = github_authenticated.permissions
                    github_permissions_dict = cast("module.Permissions", github_permissions.model_dump())
                    # Test that all permissions are in github_permissions
                    permission: module.PermissionsKey
                    for permission, access in permissions.items():  # type: ignore[assignment]
                        if permission not in github_permissions_dict or _gt_access(
                            access,
                            github_permissions_dict[permission],
                        ):
                            application["errors"].append(
                                f"Missing permission ({permission}={access}) in the GitHub application, "
                                "please add it in the GitHub configuration interface.",
                            )
                            _LOGGER.error(
                                "Missing permission (%s=%s) in the GitHub application (%s) "
                                "please add it in the GitHub configuration interface.",
                                permission,
                                access,
                                app_name,
                            )
                            _LOGGER.info(
                                "Current permissions:\n%s",
                                "\n".join([f"{k}={v}" for k, v in github_permissions_dict.items()]),
                            )
                        elif _gt_access(
                            github_permissions_dict[permission],
                            access,
                        ):
                            _LOGGER.error(
                                "The GitHub application '%s' has more permission (%s=%s) than required, "
                                "please remove it in the GitHub configuration interface.",
                                app_name,
                                permission,
                                github_permissions_dict[permission],
                            )
                    for permission_str in github_permissions_dict:
                        if permission_str not in permissions:
                            _LOGGER.error(
                                "Unnecessary permission (%s) in the GitHub application '%s', please remove it in the "
                                "GitHub configuration interface.",
                                permission_str,
                                app_name,
                            )
                else:
                    application["errors"].append("TEST_APPLICATION is set, no GitHub API call is made.")
                    _LOGGER.error(application["errors"][-1])
            except Exception as exception:
                application["errors"].append(str(exception))
                _LOGGER.exception(application["errors"][-1])

        applications.append(application)

    return {
        "request": request,
        "user": user,
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "description": configuration.APPLICATION_CONFIGURATION["description"],
        "documentation_url": configuration.APPLICATION_CONFIGURATION["documentation-url"],
        "profiles": configuration.APPLICATION_CONFIGURATION["profiles"],
        "applications": applications,
    }


HomeData = Annotated[dict[str, Any], Depends(home)]
