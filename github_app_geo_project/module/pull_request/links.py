"""Module that adds some links to the pull request message."""

import json
import os
import re
import urllib.parse
from typing import Any

import github
import github.PullRequest

from github_app_geo_project import configuration, module
from github_app_geo_project.module.pull_request import links_configuration


def _add_issue_link(
    config: links_configuration.PullRequestAddLinksConfiguration, pull_request: github.PullRequest.PullRequest
) -> None:
    """Add a comment with the link to Jira if needed."""
    body = pull_request.body or ""
    if "<!-- pull request links -->" in body.upper():
        return

    content = config.get("content", [])
    if not content:
        return

    blacklist = config.get("blacklist", {})
    values: dict[str, str] = {
        "pull-request-number": f"{pull_request.number}",
        "head-branch": pull_request.head.ref,
    }

    for uppercase_key in config.get("uppercase", []):
        if uppercase_key in values:
            values[uppercase_key] = values[uppercase_key].upper()
    for lowercase_key in config.get("lowercase", []):
        if lowercase_key in values:
            values[lowercase_key] = values[lowercase_key].lower()

    for pattern in config.get("branch-patterns", []):
        re_ = re.compile(pattern)
        match = re_.match(pull_request.head.ref)
        if match and match.groupdict():
            valid = True
            for key, value in match.groupdict().items():
                if key in blacklist and value in blacklist[key]:
                    valid = False
                    break
            if valid:
                values.update(match.groupdict())

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

    pull_request.edit(
        body=(pull_request.body + "\n".join(result)) if pull_request.body is not None else "\n".join(result)
    )


class Links(module.Module[links_configuration.PullRequestAddLinksConfiguration]):
    """Module to add some links to the pull request message and commits."""

    def title(self) -> str:
        """Get the title."""
        return "Pull request checks"

    def description(self) -> str:
        """Get the description."""
        return "Check the pull request spelling, and commits"

    def documentation_url(self) -> str:
        """Get the documentation URL."""
        return (
            "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Pull-request-checks"
        )

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
            },
            {"pull_request"},
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with open(
            os.path.join(os.path.dirname(__file__), "links-schema.json"), encoding="utf-8"
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """Get the actions to execute."""
        if (
            context.event_data.get("action") in ("opened", "reopened", "synchronize")
            and "pull_request" in context.event_data
        ):
            full_name = context.event_data.get("repository", {}).get("full_name")
            owner, repo = full_name.split("/")
            github_project = configuration.get_github_project({}, context.github_application, owner, repo)
            repo = github_project.github.get_repo(full_name)
            check_run = repo.create_check_run(
                "Pull request checks",
                context.event_data.get("pull_request", {}).get("head", {}).get("sha"),
            )
            return [
                module.Action(
                    {
                        "pull-request-number": context.event_data.get("pull_request", {}).get("number"),
                        "check-run-id": check_run.id,
                    }
                )
            ]
        return []

    def process(
        self, context: module.ProcessContext[links_configuration.PullRequestAddLinksConfiguration]
    ) -> module.ProcessOutput | None:
        """Process the module."""
        repo = context.github_project.github.get_repo(
            context.github_project.owner + "/" + context.github_project.repository
        )

        pull_request = repo.get_pull(number=context.module_data["pull-request-number"])
        _add_issue_link(context.module_config, pull_request)

        service_url = context.service_url
        service_url = service_url if service_url.endswith("/") else service_url + "/"
        service_url = urllib.parse.urljoin(service_url, "logs/")
        service_url = urllib.parse.urljoin(service_url, str(context.job_id))

        check_run = repo.get_check_run(context.module_data["check-run-id"])
        check_run.edit(status="in_progress", details_url=service_url)

        check_run.edit(
            status="completed",
            conclusion="skipped",
        )

        return module.ProcessOutput(transversal_status=context.module_data)

    def cleanup(self, context: module.CleanupContext) -> None:
        """Cleanup the module."""
        repo = context.github_project.github.get_repo(
            context.github_project.owner + "/" + context.github_project.repository
        )
        check_run = repo.get_check_run(context.module_data["check-run-id"])
        check_run.edit(
            status="completed",
            conclusion="skipped",
        )
