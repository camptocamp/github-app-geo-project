"""Module to dispatch publishing event."""

import logging
from typing import Any

import github_app_geo_project
import github_app_geo_project.views
import github_app_geo_project.views.webhook
from github_app_geo_project import module

_LOGGER = logging.getLogger(__name__)


class Webhook(module.Module[None, dict[str, Any], None, None]):
    """
    The version module.

    Create a dashboard to show the back ref versions with support check
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Webhook"

    def description(self) -> str:
        """Get the description of the module."""
        return "Manage the webhook events"

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        return {}

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={},
            events=set(),
        )

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the actions of the module.
        """
        del context
        return []

    async def process(
        self,
        context: module.ProcessContext[None, dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """
        Process the action.
        """

        await github_app_geo_project.views.webhook.process_event(context)
        return module.ProcessOutput()

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False
