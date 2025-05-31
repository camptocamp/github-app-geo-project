"""Module to outdated old comments from a bot like copilot."""

import json
import logging
from pathlib import Path
from typing import Any

import githubkit.versions.latest.models
import githubkit.versions.v2022_11_28.webhooks.pull_request_review
import githubkit.webhooks
from pydantic import BaseModel

from github_app_geo_project import module

_LOGGER = logging.getLogger(__name__)


class _Config(BaseModel):
    """The configuration of the module."""

    authors: list[list[str]] = []
    """The concerned groups of equivalent authors to outdate comments."""


class _EventData(BaseModel):
    """Module payload data related to the event."""

    author: str
    """The concerned author to outdate comments."""

    pull_request_number: int
    """The pull request number where the comments should be outdated."""

    comment_number: int
    """The new comment number that should not be outdated."""


class OutdatedComments(module.Module[_Config, _EventData, None, None]):
    """
    Module that outdate previous comments from a bot like copilot.

    Configure this module with GitHub Copilot like this:

    ```yaml
    authors:
      - - Copilot
        - copilot-pull-request-reviewer[bot]
    ```
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Outdate comments"

    def description(self) -> str:
        """Get the description of the module."""
        return "Outdate previous comments from a bot like copilot."

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Outdated-comments"

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with (Path(__file__).parent / "schema.json").open(encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={"pull_requests": "write"},
            events={"pull_request_review"},
        )

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.event_name == "pull_request_review":
            event_data = githubkit.webhooks.parse_obj("pull_request_review", context.event_data)
            if event_data.action == "submitted" and isinstance(
                event_data,
                githubkit.versions.v2022_11_28.webhooks.pull_request_review.WebhookPullRequestReviewSubmitted,  # type: ignore[attr-defined]
            ):
                return [
                    module.Action(
                        _EventData(
                            author=event_data.sender.login,
                            pull_request_number=event_data.pull_request.number,
                            comment_number=event_data.review.id,
                        ),
                    ),
                ]
        return []

    async def process(
        self,
        context: module.ProcessContext[_Config, _EventData],
    ) -> module.ProcessOutput[_EventData, None]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """

        output_messages: list[str] = []
        author_found = False
        for authors in context.module_config.authors:
            if context.module_event_data.author not in authors:
                continue

            author_found = True

            comment: githubkit.versions.latest.models.PullRequestReview
            async for comment in context.github_project.aio_github.paginate(
                context.github_project.aio_github.rest.pulls.async_list_reviews,
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                pull_number=context.module_event_data.pull_request_number,
            ):
                if (
                    comment
                    and comment.user.login in authors
                    and comment.id != context.module_event_data.comment_number
                ):
                    _LOGGER.info(
                        "Outdating comment %s from %s on pull request #%s",
                        comment.id,
                        comment.user.login,
                        context.module_event_data.pull_request_number,
                    )
                    output_messages.append(
                        f"Outdating comment: {comment.body[:50]}",
                    )

                    await context.github_project.aio_github.graphql.arequest(
                        """
                        mutation minimizeComment($subjectId: ID!) {
                            minimizeComment(input: {
                                classifier: OUTDATED,
                                subjectId: $subjectId
                            }) {
                                minimizedComment {
                                isMinimized
                                }
                            }
                        }
                        """,
                        variables={"subjectId": comment.node_id},
                    )

        if not author_found:
            return module.ProcessOutput(
                output={
                    "summary": f"Author {context.module_event_data.author} is not configured in the list of authors to outdate comments.",
                },
            )
        if not output_messages:
            return module.ProcessOutput(
                output={
                    "summary": f"No comments to outdate for author {context.module_event_data.author} on pull request #{context.module_event_data.pull_request_number}.",
                },
            )
        return module.ProcessOutput(
            output={
                "summary": f"Outdated {len(output_messages)} comments from {context.module_event_data.author} on pull request #{context.module_event_data.pull_request_number}.",
                "test": "\n".join(output_messages),
            },
        )

    destination_repository: str
    legacy: bool = False
    version_type: str
    package_type: str
    image_re: str = ".*"

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False
