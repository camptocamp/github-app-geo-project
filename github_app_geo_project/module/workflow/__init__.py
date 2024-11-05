"""Module to display the status of the workflows in the transversal dashboard."""

import logging
from typing import Any

import c2cciutils
import github
import security_md

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
            return [module.Action({}, priority=module.PRIORITY_STATUS + 2)]
        return []

    async def process(
        self, context: module.ProcessContext[None, dict[str, Any], dict[str, Any]]
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        full_repo = f"{context.github_project.owner}/{context.github_project.repository}"

        module_utils.manage_updated(context.transversal_status, full_repo, days_old=30)

        repo_data = context.transversal_status[full_repo]

        repo = context.github_project.github.get_repo(full_repo)

        stabilization_branches = [repo.default_branch]
        security_file = None
        try:
            security_file = repo.get_contents("SECURITY.md")
        except github.GithubException as exc:
            if exc.status != 404:
                raise
        if security_file is not None:
            assert isinstance(security_file, github.ContentFile.ContentFile)
            security = security_md.Security(security_file.decoded_content.decode("utf-8"))

            stabilization_branches += module_utils.get_stabilization_versions(security)

        else:
            _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")

        for key in list(repo_data.keys()):
            if key not in stabilization_branches and key != "updated":
                del repo_data[key]

        head_branch = context.event_data.get("workflow_run", {}).get("head_branch")
        if head_branch not in stabilization_branches:
            _LOGGER.info(
                "The workflow run %s is not on a stabilization branch (%s), skipping",
                head_branch,
                ", ".join(stabilization_branches),
            )
            return module.ProcessOutput()

        branch_data = repo_data.setdefault(head_branch, {})
        workflow_name = context.event_data.get("workflow", {}).get("name", "Un named")
        if context.event_data.get("workflow_run", {}).get("conclusion") == "success":
            if workflow_name in branch_data:
                del branch_data[workflow_name]
            if not branch_data:
                del repo_data[head_branch]
            if repo_data.keys() == {"updated"}:
                del context.transversal_status[
                    context.github_project.owner + "/" + context.github_project.repository
                ]
            _LOGGER.info(
                "Workflow '%s' is successful, removing it from the status",
                workflow_name,
            )
            return module.ProcessOutput(transversal_status=context.transversal_status)

        workflow_data = {
            "url": context.event_data.get("workflow_run", {}).get("html_url"),
            "date": context.event_data.get("workflow_run", {}).get("created_at"),
            "jobs": [],
        }
        branch_data[workflow_name] = workflow_data
        _LOGGER.info(
            "Workflow '%s' is not successful, adding it to the status",
            workflow_name,
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
        message = module_utils.HtmlMessage(utils.format_json(context.transversal_status))
        message.title = "New transversal status"
        _LOGGER.debug(message)
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
            data={"status": context.status},
        )
