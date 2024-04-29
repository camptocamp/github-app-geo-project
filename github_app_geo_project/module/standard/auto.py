"""Utility functions for the auto* modules."""

import json
import logging
import os
import re
from abc import abstractmethod
from typing import Any, cast

import github

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


class Auto(module.Module[auto_configuration.AutoPullRequest]):
    """The auto module."""

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Auto-review-merge-close"

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        event_data = context.event_data
        if (
            event_data.get("action") in ("opened", "reopened")
            and event_data.get("pull_request", {}).get("state") == "open"
        ):
            return [module.Action(priority=module.PRIORITY_STANDARD, data={}, checks=False)]

        return []

    @abstractmethod
    def do_action(
        self,
        context: module.ProcessContext[auto_configuration.AutoPullRequest],
        pull_request: github.PullRequest.PullRequest,
    ) -> None:
        """Process the action, called it the conditions match."""

    def process(self, context: module.ProcessContext[auto_configuration.AutoPullRequest]) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        for condition in context.module_config.get("conditions", []):
            if (
                equals_if_defined(
                    condition.get("author"),
                    cast(str, context.event_data["pull_request"]["user"]["login"]),
                )
                and get_re(condition.get("title")).match(context.event_data["pull_request"]["title"])
                and get_re(condition.get("branch")).match(context.event_data["pull_request"]["head"]["ref"])
            ):
                repository = context.github_project.github.get_repo(
                    context.event_data["repository"]["full_name"]
                )
                pull_request = repository.get_pull(context.event_data["pull_request"]["number"])
                self.do_action(context, pull_request)
                return

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        with open(
            os.path.join(os.path.dirname(__file__), "auto-schema.json"), encoding="utf-8"
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
