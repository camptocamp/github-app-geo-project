"""Module to display the status of the workflows in the transversal dashboard."""

import datetime
import json
import logging
from typing import Any

import c2cciutils
import github
import pygments.formatters
import pygments.lexers

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class Workflow(module.Module[None]):
    """Module to display the status of the workflows in the transversal dashboard."""

    def title(self) -> str:
        return "Workflow dashboard"

    def description(self) -> str:
        return "Display the status of the workflows in the transversal dashboard"

    def documentation_url(self) -> str:
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Workflow"

    def get_json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        if (
            context.event_data.get("action") != "completed"
            and "workflow_run" in context.event_data
            and "event" in context.event_data["workflow_run"]
            and context.event_data["workflow_run"]["event"] != "pull_request"
        ):
            return [module.Action({})]
        return []

    def process(self, context: module.ProcessContext[None]) -> module.ProcessOutput | None:
        repo_data = context.module_data.setdefault(
            context.github_project.owner + "/" + context.github_project.repository, {}
        )
        repo_data.setdefault(f"{context.github_project.owner}/{context.github_project.repository}", {})[
            "updated"
        ] = datetime.datetime.now().isoformat()
        for other_repo in context.module_data:
            if "updated" not in context.module_data[other_repo] or datetime.datetime.fromisoformat(
                context.module_data[other_repo]["updated"]
            ) < datetime.datetime.now() - datetime.timedelta(days=30):
                del context.module_data[other_repo]

        repo = context.github_project.github.get_repo(
            context.github_project.owner + "/" + context.github_project.repository
        )

        stabilization_branches = [repo.default_branch]
        security_file = None
        try:
            security_file = repo.get_contents("SECURITY.md")
        except github.GithubException as exc:
            if exc.status != 404:
                raise
        if security_file is not None:
            assert isinstance(security_file, github.ContentFile.ContentFile)
            security = c2cciutils.security.Security(security_file.decoded_content.decode("utf-8"))

            stabilization_branches += module_utils.get_stabilization_branch(security)

        else:
            _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")
            stabilization_branches = [repo.default_branch]

        for key in repo_data:
            if key not in stabilization_branches:
                del repo_data[key]

        if context.event_data.get("workflow_run", {}).get("head_branch") not in stabilization_branches:
            return None

        branch_data = repo_data.setdefault(context.event_data.get("workflow_run", {}).get("head_branch"), {})
        if context.event_data.get("workflow_run", {}).get("conclusion") == "success":
            del branch_data[context.event_data.get("workflow", {}).get("name", "-")]
            if not branch_data:
                del repo_data[context.event_data.get("workflow_run", {}).get("head_branch")]
            return module.ProcessOutput(repo_data)

        workflow_data = {
            "url": context.event_data.get("workflow_run", {}).get("html_url"),
            "date": context.event_data.get("workflow_run", {}).get("created_at"),
            "jobs": [],
        }
        branch_data[context.event_data.get("workflow", {}).get("name", "-")] = workflow_data

        workflow_run = repo.get_workflow_run(context.event_data.get("workflow_run", {}).get("id"))
        jobs = workflow_run.jobs()

        for job in jobs:
            if job.conclusion != "success":
                workflow_data["jobs"].append({"name": job.name, "run_url": job.html_url})

        return super().process(context)

    def has_transversal_dashboard(self) -> bool:
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext
    ) -> module.TransversalDashboardOutput:
        if "repository" in context.params:
            lexer = pygments.lexers.JsonLexer()
            formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")
            data = pygments.highlight(
                json.dumps(context.status.get(context.params["repository"], {}), indent=4), lexer, formatter
            )

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/workflow/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "data": data,
                },
            )

        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/workflow/dashboard.html",
            data={"repositories": context.status.keys()},
        )
