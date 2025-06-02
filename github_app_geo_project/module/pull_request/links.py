"""Module that adds some links to the pull request message."""

import json
import re
from pathlib import Path
from typing import Any

import githubkit.versions.latest.models
import githubkit.webhooks

from github_app_geo_project import module
from github_app_geo_project.module.pull_request import links_configuration


async def _add_issue_link(
    context: module.ProcessContext[
        links_configuration.PullRequestAddLinksConfiguration,
        dict[str, Any],
    ],
) -> str:
    """Add a comment with the link to Jira if needed."""

    # Get pull request
    pull_request = (
        await context.github_project.aio_github.rest.pulls.async_get(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            pull_number=context.module_event_data["pull-request-number"],
        )
    ).parsed_data

    body = pull_request.body or ""
    if "<!-- pull request links -->" in body:
        return "Pull request links already added."

    content = context.module_config.get("content", [])
    if not content:
        return "Empty configuration."

    values: dict[str, str] = {
        "pull_request_number": f"{pull_request.number}",
        "head_branch": pull_request.head.ref,
    }

    for pattern in context.module_config.get("branch-patterns", []):
        re_ = re.compile(pattern)
        match = re_.match(pull_request.head.ref)
        if match and match.groupdict():
            values.update(match.groupdict())

    for uppercase_key in context.module_config.get("uppercase", []):
        if uppercase_key in values:
            values[uppercase_key] = values[uppercase_key].upper()
    for lowercase_key in context.module_config.get("lowercase", []):
        if lowercase_key in values:
            values[lowercase_key] = values[lowercase_key].lower()

    blacklist = context.module_config.get("blacklist", {})
    for key, value in list(values.items()):
        if key in blacklist and value in blacklist[key]:
            del values[key]

    result = ["<!-- pull request links -->"]
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

    if len(result) == 1:
        return "Nothing to add."

    # Update the pull request
    await context.github_project.aio_github.rest.pulls.async_update(
        owner=context.github_project.owner,
        repo=context.github_project.repository,
        pull_number=pull_request.number,
        body="\n".join([body, "", *result]),
    )
    return "Pull request descriptions updated."


class Links(
    module.Module[links_configuration.PullRequestAddLinksConfiguration, dict[str, Any], dict[str, Any], None],
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
        if context.event_name == "pull_request":
            event_data = githubkit.webhooks.parse_obj("pull_request", context.event_data)
            if event_data.action in ("opened", "reopened", "synchronize"):
                return [
                    module.Action(
                        {
                            "pull-request-number": event_data.pull_request.number,
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
        ],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """Process the module."""

        # Get the new body with added links
        message = await _add_issue_link(context)

        return module.ProcessOutput(output={"summary": message})
