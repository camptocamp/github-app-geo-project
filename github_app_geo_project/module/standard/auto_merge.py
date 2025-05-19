"""Module to generate the changelog on a release of a version."""

import logging
from typing import Any

import githubkit.versions.latest.models

from github_app_geo_project import module
from github_app_geo_project.module.standard import auto, auto_configuration

_LOGGER = logging.getLogger(__name__)


class AutoMerge(auto.Auto):
    """The auto merge pull request module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Activate the auto merge on the pull request"

    def description(self) -> str:
        """Get the description of the module."""
        return "If a pull request match one of the conditions, the auto merge will be activated"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "read",
                "workflows": "write",
            },
            {
                "pull_request",
            },
        )

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
        # Enable auto-merge
        await context.github_project.aio_github.graphql.arequest(
            """
            mutation EnableAutoMerge($pullRequestId: ID!) {
                enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: MERGE}) {
                    clientMutationId
                }
            }
            """,
            variables={"pullRequestId": pull_request.node_id},
        )
