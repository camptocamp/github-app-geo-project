"""
The updates module.
"""

import base64
import json
import logging
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, cast

import githubkit.exception
import githubkit.versions.latest.models
import multi_repo_automation as mra
import security_md
import yaml
from pydantic import BaseModel

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.updates import configuration

_LOGGER = logging.getLogger(__name__)


class Step(Enum):
    """The step of the updates process."""

    INITIAL = "initial"
    BRANCH = "branch"


class UpdatesEventData(BaseModel):
    """The event data for the updates module."""

    step: Step = Step.INITIAL
    branch: str | None = None


class UpdatesTransversalStatus(BaseModel):
    """The transversal status for the updates module."""


class UpdatesIntermediateStatus(BaseModel):
    """The intermediate status for the updates module."""


class Updates(
    module.Module[
        configuration.UpdatesConfiguration,
        UpdatesEventData,
        UpdatesTransversalStatus,
        UpdatesIntermediateStatus,
    ],
):
    """
    The updates module.
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Updates"

    def description(self) -> str:
        """Get the description of the module."""
        return "Apply automated updates to repositories"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/updates/README.md"

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[UpdatesEventData]]:
        """
        Get the action related to the module and the event.
        """
        if (
            context.github_event_data.get("type") == "event"
            and context.github_event_data.get("name") == "updates-cron"
        ):
            return [
                module.Action(data=UpdatesEventData(step=Step.INITIAL), priority=module.PRIORITY_CRON),
            ]
        return []

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        with (Path(__file__).parent / "schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return cast("dict[str, Any]", schema)

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={
                "contents": "write",
                "pull_requests": "write",
            },
            events=set(),
        )

    async def process(
        self,
        context: module.ProcessContext[configuration.UpdatesConfiguration, UpdatesEventData],
    ) -> module.ProcessOutput[UpdatesEventData, UpdatesIntermediateStatus]:
        """
        Process the action.
        """
        if context.module_event_data.step == Step.INITIAL:
            repo = context.github_project.repository
            owner = context.github_project.owner

            branches = [await context.github_project.default_branch()]
            try:
                security_file = await context.github_project.aio_github.rest.repos.async_get_content(
                    owner=owner,
                    repo=repo,
                    path="SECURITY.md",
                )
                if not isinstance(
                    security_file.parsed_data,
                    githubkit.versions.latest.models.ContentFile,
                ):
                    message = "SECURITY.md is not a file"
                    raise TypeError(message)

                security = security_md.Security(
                    base64.b64decode(security_file.parsed_data.content).decode("utf-8")
                )
                branches.extend(security.branches())
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code != 404:
                    raise

            # Deduplicate branches
            branches = sorted(set(branches))

            return module.ProcessOutput(
                actions=[
                    module.Action(
                        data=UpdatesEventData(step=Step.BRANCH, branch=branch),
                        priority=module.PRIORITY_CRON,
                    )
                    for branch in branches
                ]
            )
        if context.module_event_data.step == Step.BRANCH:
            assert context.module_event_data.branch is not None
            await self._process_branch(context, context.module_event_data.branch)

        return module.ProcessOutput()

    async def _process_branch(
        self,
        context: module.ProcessContext[configuration.UpdatesConfiguration, UpdatesEventData],
        branch: str,
    ) -> None:
        with (Path(__file__).parent / "versions.yaml").open(encoding="utf-8") as versions_file:
            versions = yaml.safe_load(versions_file)

        with tempfile.TemporaryDirectory() as tmpdirname:
            cwd = Path(tmpdirname)
            # Clone the repository
            if os.environ.get("TEST") == "TRUE":
                # In test mode, we don't clone
                pass
            else:
                cloned_cwd = await module_utils.git_clone(context.github_project, branch, cwd)
                if cloned_cwd is None:
                    _LOGGER.error(
                        "Failed to clone repository %s on branch %s",
                        context.github_project.repository,
                        branch,
                    )
                    return
                cwd = cloned_cwd

            updated = False

            config_file = cwd / ".pre-commit-config.yaml"
            if config_file.exists():
                with mra.EditYAML(config_file) as config:
                    for repo_config in config.get("repos", []):
                        if (
                            repo_config.get("repo") == "https://github.com/mheap/json-schema-spell-checker"
                            and repo_config.get("rev") == "main"
                        ):
                            repo_config["rev"] = versions["mheap/json-schema-spell-checker"]
                            updated = True

            if updated:
                # Check if there are changes using git
                # Since we modified the file in place, we can read it back.
                # But to know if it's different from HEAD, we can rely on git diff or just try to create the update.
                # The logic below creates a branch and PR.

                branch_name = f"ghci/update/{branch}"

                message = f"Update the project by GHCI on branch {branch}"

                await module_utils.create_commit_pull_request(
                    branch,
                    branch_name,
                    message,
                    message,
                    context.github_project,
                    cwd,
                )
