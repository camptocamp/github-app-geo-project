"""Utility functions for the auto* modules."""

import datetime
import json
import logging
import os.path
from typing import Any

from numpy import number

from github_app_geo_project import module
from github_app_geo_project.module import utils
from github_app_geo_project.module.delete_old_workflow_runs import configuration

_LOGGER = logging.getLogger(__name__)


class DeleteOldWorkflowRuns(
    module.Module[configuration.DeleteOldWorkflowRunsConfiguration, dict[str, Any], dict[str, Any]]
):
    """Delete old workflow jobs."""

    def title(self) -> str:
        """Define the title of the module."""
        return "Delete old workflow jobs"

    def description(self) -> str:
        """Get the description of the module."""
        return "Delete old workflow jobs"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Delete-Old-Workflow-Job"

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.event_data.get("type") == "event" and context.event_data.get("name") == "daily":
            return [module.Action(data={})]
        return []

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        return module.GitHubApplicationPermissions(permissions={"actions": "write"}, events=set())

    async def process(
        self,
        context: module.ProcessContext[
            configuration.DeleteOldWorkflowRunsConfiguration, dict[str, Any], dict[str, Any]
        ],
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repo = context.github_project.github.get_repo(
            f"{context.github_project.owner}/{context.github_project.repository}"
        )

        deleted_number = 0
        deleted_workflows = []

        for rule in context.module_config.get("rules", []):
            older_than_days = rule.get("older-than-days", 0)
            workflow = rule.get("workflow")
            actor = rule.get("actor")
            branch = rule.get("branch")
            event = rule.get("event")
            status = rule.get("status")

            arguments = {
                "created": f"<{datetime.datetime.now() - datetime.timedelta(days=older_than_days):%Y-%m-%d}",
            }
            if actor:
                arguments["actor"] = actor
            if branch:
                arguments["branch"] = branch
            if event:
                arguments["event"] = event
            if status:
                arguments["status"] = status

            _LOGGER.info(
                "Deleting workflow runs with arguments:\n%s",
                "\n".join([": ".join(a) for a in arguments.items()]),
            )
            workflow_runs = repo.get_workflow_runs(**arguments)  # type: ignore[arg-type]
            for workflow_run in workflow_runs:
                if not workflow or workflow_run.name == workflow:
                    deleted_workflows.append(f"{workflow_run.name} ({workflow_run.created_at})")
                    workflow_run.delete()
                    deleted_number += 1
        message = utils.HtmlMessage("<br>\n".join(deleted_workflows))
        message.title = f"Deleted {deleted_number} workflow runs"
        _LOGGER.info(message)

        return module.ProcessOutput()
