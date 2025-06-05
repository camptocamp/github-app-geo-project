"""Utility functions for the auto* modules."""

import json
import logging
import re
from abc import abstractmethod
from pathlib import Path
from typing import Any

import githubkit
import githubkit.versions.latest.models
import githubkit.webhooks

from github_app_geo_project import module
from github_app_geo_project.module.standard import auto_configuration

_LOGGER = logging.getLogger(__name__)


def get_re(re_str: str | None) -> re.Pattern[str]:
    """Get the compiled regex from a string."""
    if re_str is None or re_str == ".*":
        return re.compile(".*")

    if not re_str.startswith("^"):
        re_str = f"^{re_str}"
    if not re_str.endswith("$"):
        re_str = f"{re_str}$"

    return re.compile(re_str)


def equals_if_defined(reference: str | None, value: str) -> bool:
    """If the reference is defined check if it's equals to the value."""
    if reference is None:
        return True
    return reference == value


class Auto(
    module.Module[
        auto_configuration.AutoPullRequest,
        dict[str, Any],
        dict[str, Any],
        None,
    ],
):
    """The auto module."""

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Auto-review-merge-close"

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.module_event_name == "pull_request":
            event_data = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            if event_data.action in ("opened", "reopened") and event_data.pull_request.state == "open":
                return [
                    module.Action(
                        priority=module.PRIORITY_STANDARD,
                        data={},
                        checks=False,
                    ),
                ]

        return []

    @abstractmethod
    async def do_action(
        self,
        context: module.ProcessContext[
            auto_configuration.AutoPullRequest,
            dict[str, Any],
        ],
        pull_request: githubkit.versions.latest.models.PullRequest,
    ) -> None:
        """Process the action, called it the conditions match."""

    async def process(
        self,
        context: module.ProcessContext[
            auto_configuration.AutoPullRequest,
            dict[str, Any],
        ],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        assert context.module_event_name == "pull_request"
        event_data = githubkit.webhooks.parse_obj(
            "pull_request",
            context.github_event_data,
        )
        for condition in context.module_config.get("conditions", []):
            if (
                equals_if_defined(
                    condition.get("author"),
                    (event_data.pull_request.user.login if event_data.pull_request.user else ""),
                )
                and get_re(condition.get("title")).match(event_data.pull_request.title)
                and get_re(condition.get("branch")).match(
                    event_data.pull_request.head.ref,
                )
            ):
                # Get pull request
                pull_request = (
                    await context.github_project.aio_github.rest.pulls.async_get(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        pull_number=event_data.pull_request.number,
                    )
                ).parsed_data
                await self.do_action(context, pull_request)
                return module.ProcessOutput()
        return module.ProcessOutput()

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        with (Path(__file__).parent / "auto-schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            return json.loads(schema_file.read()).get("definitions", {}).get("auto")  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
            },
            {
                "pull_request",
            },
        )
