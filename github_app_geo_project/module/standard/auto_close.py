"""Module to generate the changelog on a release of a version."""

import logging
from typing import Any

import githubkit.versions.latest.models

from github_app_geo_project import module
from github_app_geo_project.module.standard import auto, auto_configuration

_LOGGER = logging.getLogger(__name__)


class AutoClose(auto.Auto):
    """The auto close pull request module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Auto close the pull request"

    def description(self) -> str:
        """Get the description of the module."""
        return "If a pull request match one of the conditions, he will be closed"

    async def do_action(
        self,
        context: module.ProcessContext[
            auto_configuration.AutoPullRequest,
            dict[str, Any],
        ],
        pull_request: githubkit.versions.latest.models.PullRequest,
    ) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        # Update the pull request state
        await context.github_project.aio_github.rest.pulls.async_update(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            pull_number=pull_request.number,
            state="closed",
        )
