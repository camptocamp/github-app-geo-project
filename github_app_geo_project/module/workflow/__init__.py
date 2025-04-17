"""Module to display the status of the workflows in the transversal dashboard."""

import logging
from typing import Any

import github
import githubkit.webhooks
import security_md

from github_app_geo_project import module, utils
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class Workflow(module.Module[None, dict[str, Any], dict[str, Any], None]):
    """Module to display the status of the workflows in the transversal dashboard."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Workflow dashboard"

    def description(self) -> str:
        """Get the description of the module."""
        return "Display the status of the workflows in the transversal dashboard"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Workflow"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "workflows": "read",
            },
            {"workflow_run"},
        )

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """Get the action related to the module and the event."""
        if context.event_name == "workflow_run":
            event_data = githubkit.webhooks.parse_obj("workflow_run", context.event_data)
            if event_data.action == "completed" and event_data.workflow_run.event != "pull_request":
                return [module.Action({}, priority=module.PRIORITY_STATUS + 2)]
        return []

    async def process(
        self,
        context: module.ProcessContext[None, dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """Process the action."""
        del context  # Unused
        return module.ProcessOutput(updated_transversal_status=True)

    async def update_transversal_status(
        self,
        context: module.ProcessContext[None, dict[str, Any]],
        intermediate_status: None,
        transversal_status: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Update the transversal status."""
        del intermediate_status
        full_repo = f"{context.github_project.owner}/{context.github_project.repository}"

        module_utils.manage_updated(transversal_status, full_repo, days_old=30)

        repo_data = transversal_status[full_repo]

        repo = context.github_project.repo

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

        assert context.event_name == "workflow_run"
        event_data = githubkit.webhooks.parse_obj("workflow_run", context.event_data)
        head_branch = event_data.workflow_run.head_branch
        if head_branch not in stabilization_branches:
            _LOGGER.info(
                "The workflow run %s is not on a stabilization branch (%s), skipping",
                head_branch,
                ", ".join(stabilization_branches),
            )
            return None

        branch_data = repo_data.setdefault(head_branch, {})
        workflow_name = event_data.workflow.name if event_data.workflow else "Unnamed"
        if event_data.workflow_run.conclusion == "success":
            if workflow_name in branch_data:
                del branch_data[workflow_name]
            if not branch_data:
                del repo_data[head_branch]
            if repo_data.keys() == {"updated"}:
                del transversal_status[full_repo]
            _LOGGER.info(
                "Workflow '%s' is successful, removing it from the status",
                workflow_name,
            )
            return transversal_status

        workflow_data_jobs: list[Any] = []
        workflow_data = {
            "url": event_data.workflow_run.html_url,
            "date": event_data.workflow_run.created_at.isoformat(),
            "jobs": workflow_data_jobs,
        }
        branch_data[workflow_name] = workflow_data
        _LOGGER.info(
            "Workflow '%s' is not successful, adding it to the status",
            workflow_name,
        )

        workflow_run = repo.get_workflow_run(event_data.workflow_run.id)
        jobs = workflow_run.jobs()
        workflow_data_jobs.extend(
            {"name": job.name, "run_url": job.html_url} for job in jobs if job.conclusion != "success"
        )

        if repo_data.keys() == ["updated"]:
            del transversal_status[full_repo]
        message = module_utils.HtmlMessage(utils.format_json(transversal_status))
        message.title = "New transversal status"
        _LOGGER.debug(message)
        return transversal_status

    def has_transversal_dashboard(self) -> bool:
        """Return True."""
        return True

    def get_transversal_dashboard(
        self,
        context: module.TransversalDashboardContext[dict[str, Any]],
    ) -> module.TransversalDashboardOutput:
        """Return the transversal dashboard output."""
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
