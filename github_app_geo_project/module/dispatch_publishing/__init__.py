"""Module to dispatch publishing event."""

import json
import logging
import os
import re
from typing import Any

import githubkit.webhooks
from pydantic import BaseModel

from github_app_geo_project import module

_LOGGER = logging.getLogger(__name__)


class _Destination(BaseModel):
    """The destination to dispatch to."""

    destination_repository: str
    """The repository to dispatch to"""
    event_type: str
    """The event type to dispatch"""
    legacy: bool = False
    """Transform the content to the legacy format"""
    version_type: str | None = None
    """The version type to dispatch"""
    package_type: str | None = None
    """The package type to dispatch"""
    image_re: str = ".*"
    """The image regular expression to dispatch"""


class _Config(BaseModel):
    """The configuration of the module."""

    destinations: list[_Destination] = []
    """The destinations to dispatch to"""


try:
    CONFIG = _Config(**json.loads(os.environ.get("DISPATCH_PUBLISH_CONFIG", "{}")))
except Exception:  # pylint: disable=broad-exception-caught
    _LOGGER.exception("Error loading the configuration")
    CONFIG = _Config()


class DispatchPublishing(module.Module[None, None, None, None]):
    """
    The version module.

    Create a dashboard to show the back ref versions with support check
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Dispatch"

    def description(self) -> str:
        """Get the description of the module."""
        return "Dispatch publishing event"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Dispatch-Publish"

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        return {}

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={"contents": "write"},
            events={"repository_dispatch"},
        )

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[None]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.event_name == "repository_dispatch":
            event_data = githubkit.webhooks.parse_obj("repository_dispatch", context.event_data)
            if event_data.action == "published" and isinstance(
                event_data,
                githubkit.versions.v2022_11_28.webhooks.RepositoryDispatchEvent,
            ):
                return [module.Action(None)]
        return []

    async def process(
        self,
        context: module.ProcessContext[None, None],
    ) -> module.ProcessOutput[None, None]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """

        assert context.event_name == "repository_dispatch"
        content = context.event_data.get("client_payload", {}).get("content", {})

        for destination in CONFIG.destinations:
            if destination.version_type and destination.package_type != content.get("version_type"):
                continue

            image_re = re.compile(destination.image_re)
            payload: dict[str, Any] = {}
            names = []

            for item in content.get("items", []):
                if destination.package_type and destination.package_type != item.get("package_type"):
                    continue

                if not image_re.match(item.get("image", "")):
                    continue

                if destination.legacy:
                    if "image" in item:
                        if item.get("repository", "") in ("", "docker.io"):
                            names.append(item["image"])
                        else:
                            names.append(f"{item['repository']}/{item['image']}")
                else:
                    payload.setdefault("content", {}).setdefault("items", []).append(item)

            if destination.legacy and names:
                payload["name"] = " ".join(names)

            if payload:
                repo = await context.github_project.aio_github.rest.repos.async_get(
                    *destination.destination_repository.split("/"),
                )
                await context.github_project.aio_github.rest.repos.async_create_dispatch_event(
                    repo.parsed_data.owner.login,
                    repo.parsed_data.name,
                    event_type=destination.event_type,
                    client_payload=payload,
                )
        return module.ProcessOutput()

    destination_repository: str
    legacy: bool = False
    version_type: str
    package_type: str
    image_re: str = ".*"

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False
