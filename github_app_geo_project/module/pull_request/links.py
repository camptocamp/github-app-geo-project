"""Module that adds some links to the pull request message."""

import json
import re
from pathlib import Path
from typing import Any

import github
import github.PullRequest

from github_app_geo_project import module
from github_app_geo_project.module.pull_request import links_configuration


def _add_issue_link(
    config: links_configuration.PullRequestAddLinksConfiguration,
    pull_request: github.PullRequest.PullRequest,
) -> str:
    """Add a comment with the link to Jira if needed."""
    body = pull_request.body or ""
    if "<!-- pull request links -->" in body:
        return "Pull request links already added."

    content = config.get("content", [])
    if not content:
        return "Empty configuration."

    values: dict[str, str] = {
        "pull_request_number": f"{pull_request.number}",
        "head_branch": pull_request.head.ref,
    }

    for pattern in config.get("branch-patterns", []):
        re_ = re.compile(pattern)
        match = re_.match(pull_request.head.ref)
        if match and match.groupdict():
            values.update(match.groupdict())

    for uppercase_key in config.get("uppercase", []):
        if uppercase_key in values:
            values[uppercase_key] = values[uppercase_key].upper()
    for lowercase_key in config.get("lowercase", []):
        if lowercase_key in values:
            values[lowercase_key] = values[lowercase_key].lower()

    blacklist = config.get("blacklist", {})
    for key, value in list(values.items()):
        if key in blacklist and value in blacklist[key]:
            del values[key]

    result = ["", "<!-- pull request links -->"]
    for link in content:
        add = True
        for require in link.get("requires", []):
            if require not in values:
                add = False
                break
        if not add:
            continue

        title = link.get("text", "").format(**values)
        if link.get("url"):
            url = link["url"].format(**values)
            result.append(f"[{title}]({url})")
        else:
            result.append(title)

    if len(result) == 2:
        return "Nothing to add."

    pull_request.edit(
        body=(pull_request.body + "\n".join(result)) if pull_request.body is not None else "\n".join(result),
    )
    return "Pull request descriptions updated."


class Links(
    module.Module[links_configuration.PullRequestAddLinksConfiguration, dict[str, Any], dict[str, Any]],
):
    """Module to add some links to the pull request message and commits."""

    def title(self) -> str:
        """Get the title."""
        return "Pull request links"

    def description(self) -> str:
        """Get the description."""
        return "Check the pull request spelling, and commits"

    def documentation_url(self) -> str:
        """Get the documentation URL."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Pull-request-links"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
            },
            {"pull_request"},
        )

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with (Path(__file__).parent / "links-schema.json").open(encoding="utf-8") as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """Get the actions to execute."""
        if (
            context.event_data.get("action") in ("opened", "reopened", "synchronize")
            and "pull_request" in context.event_data
        ):
            return [
                module.Action(
                    {
                        "pull-request-number": context.event_data.get("pull_request", {}).get("number"),
                    },
                    priority=module.PRIORITY_STATUS,
                ),
            ]
        return []

    async def process(
        self,
        context: module.ProcessContext[
            links_configuration.PullRequestAddLinksConfiguration,
            dict[str, Any],
            dict[str, Any],
        ],
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        """Process the module."""
        repo = context.github_project.repo
        pull_request = repo.get_pull(number=context.module_event_data["pull-request-number"])
        message = _add_issue_link(context.module_config, pull_request)
        return module.ProcessOutput(output={"summary": message})
