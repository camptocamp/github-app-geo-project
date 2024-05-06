"""Module to display the status of the workflows in the transversal dashboard."""

import logging
from typing import Any

import c2cciutils
import github

from github_app_geo_project import module, utils
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class Workflow(module.Module[None, dict[str, Any], dict[str, Any]]):
    """Module to display the status of the workflows in the transversal dashboard."""

    def title(self) -> str:
        return "Workflow dashboard"

    def description(self) -> str:
        return "Display the status of the workflows in the transversal dashboard"

    def documentation_url(self) -> str:
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Workflow"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        return module.GitHubApplicationPermissions(
            {
                "workflows": "read",
            },
            {"workflow_run"},
        )

    def get_json_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        if (
            context.event_data.get("action") == "completed"
            and context.event_data.get("workflow_run", {}).get("event", "pull_request") != "pull_request"
        ):
            return [module.Action({})]
        return []

    async def process(
        self, context: module.ProcessContext[None, dict[str, Any], dict[str, Any]]
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        repo_data = context.transversal_status.setdefault(
            context.github_project.owner + "/" + context.github_project.repository, {}
        )

        module_utils.manage_updated(
            context.transversal_status,
            f"{context.github_project.owner}/{context.github_project.repository}",
            days_old=30,
        )

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

        for key in list(repo_data.keys()):
            if key not in stabilization_branches or key == "updated":
                del repo_data[key]

        if context.event_data.get("workflow_run", {}).get("head_branch") not in stabilization_branches:
            _LOGGER.info(
                "The workflow run %s is not on a stabilization branch (%s), skipping",
                context.event_data.get("workflow_run", {}).get("head_branch"),
                ", ".join(stabilization_branches),
            )
            return module.ProcessOutput()

        branch_data = repo_data.setdefault(context.event_data.get("workflow_run", {}).get("head_branch"), {})
        if (
            context.event_data.get("workflow_run", {}).get("conclusion") == "success"
            and context.event_data.get("workflow", {}).get("name", "-") in branch_data
        ):
            del branch_data[context.event_data.get("workflow", {}).get("name", "-")]
            if not branch_data:
                del repo_data[context.event_data.get("workflow_run", {}).get("head_branch")]
            if repo_data.keys() == ["updated"]:
                del context.transversal_status[
                    context.github_project.owner + "/" + context.github_project.repository
                ]
            _LOGGER.info(
                "Workflow '%s' is successful, removing it from the status",
                context.event_data.get("workflow", {}).get("name", "-"),
            )
            return module.ProcessOutput(transversal_status=context.transversal_status)

        workflow_data = {
            "url": context.event_data.get("workflow_run", {}).get("html_url"),
            "date": context.event_data.get("workflow_run", {}).get("created_at"),
            "jobs": [],
        }
        branch_data[context.event_data.get("workflow", {}).get("name", "-")] = workflow_data
        _LOGGER.info(
            "Workflow '%s' is not successful, adding it to the status",
            context.event_data.get("workflow", {}).get("name", "-"),
        )

        workflow_run = repo.get_workflow_run(context.event_data.get("workflow_run", {}).get("id"))
        jobs = workflow_run.jobs()

        for job in jobs:
            if job.conclusion != "success":
                workflow_data["jobs"].append({"name": job.name, "run_url": job.html_url})

        if repo_data.keys() == ["updated"]:
            del context.transversal_status[
                context.github_project.owner + "/" + context.github_project.repository
            ]
        return module.ProcessOutput(transversal_status=context.transversal_status)

    def has_transversal_dashboard(self) -> bool:
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext[dict[str, Any]]
    ) -> module.TransversalDashboardOutput:
        if "repository" in context.params:
            data = utils.format_json(context.status.get(context.params["repository"], {}))

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/workflow/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "data_json": context.status.get(context.params["repository"], {}),
                    "data": data,
                },
            )

        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/workflow/dashboard.html",
            data={"repositories": context.status.keys()},
        )
