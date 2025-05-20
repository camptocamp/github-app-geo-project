"""Module to generate the changelog on a release of a version."""

import logging
from typing import Any

import githubkit.versions.latest.models

from github_app_geo_project import module
from github_app_geo_project.module.standard import auto, auto_configuration

_LOGGER = logging.getLogger(__name__)


class AutoReview(auto.Auto):
    """
    The auto review pull request module.

    This is not working as expected because it's not possible to be consider as approver
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Auto review a pull request"

    def description(self) -> str:
        """Get the description of the module."""
        return "If a pull request match one of the conditions, he will get a accept review"

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
        # Create review using GitHubKit async API
        await context.github_project.aio_github.rest.pulls.async_create_review(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            pull_number=pull_request.number,
            event="APPROVE",
        )
