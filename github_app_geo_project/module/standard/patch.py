"""Utility functions for the auto* modules."""

import io
import logging
import os
import subprocess  # nosec
import tempfile
import zipfile
from typing import Any, cast

import requests

from github_app_geo_project import module
from github_app_geo_project.module import utils

_LOGGER = logging.getLogger(__name__)


class Patch(module.Module[dict[str, Any]]):
    """Module that apply the patch present in the artifact on the branch of the pull request."""

    def title(self) -> str:
        return "Apply the patch from the artifacts"

    def description(self) -> str:
        return "This module apply the patch present in the artifact on the branch of the pull request."

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Patch-module"

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        return [module.Action(priority=module.PRIORITY_CRON, data={})]

    def process(self, context: module.ProcessContext[dict[str, Any]]) -> str | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repo = context.github_application.get_repo(context.repository)
        workflow_run = repo.get_workflow_run(
            cast(int, context.event_data["workflow_run"]["id"])  # type: ignore[index,call-overload]
        )
        if workflow_run.status != "completed" or workflow_run.conclusion != "failure":
            return None

        assert context.github_application.__requester.__auth is not None  # pylint: disable=protected-access
        token = context.github_application.__requester.__auth.token  # pylint: disable=protected-access
        should_push = False
        for artifact in workflow_run.get_artifacts():
            if not artifact.name.endswith(".patch"):
                continue
            response = requests.get(artifact.archive_download_url, timeout=120)
            if not response.ok:
                _LOGGER.error("Failed to download artifact %s", artifact.name)
                continue

            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                    [
                        "git",
                        "clone",
                        "--depth=1",
                        f"--branch={workflow_run.head_branch}",
                        f"https://x-access-token:{token}@github.com/{context.repository}.git",
                    ],
                    capture_output=True,
                    encoding="utf-8",
                )
                if proc.returncode != 0:
                    _LOGGER.error("Failed to clone the repository\n%s\n%s", proc.stdout, proc.stderr)
                    return None
                os.chdir(context.repository.split("/")[-1])
                # unzip
                with zipfile.ZipFile(io.BytesIO(response.content)) as diff:
                    if len(diff.namelist()) != 1:
                        _LOGGER.info("Invalid artifact %s", artifact.name)
                        continue

                    with diff.open(diff.namelist()[0]) as file:
                        subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["patch"],
                            input=file.read().decode("utf-8"),
                            encoding="utf-8",
                            capture_output=True,
                        )
                        if proc.returncode != 0:
                            _LOGGER.error("Failed to apply the diff\n%s\n%s", proc.stdout, proc.stderr)
                            return None
                        error = utils.create_commit(artifact.name[:-5])
                        if error:
                            _LOGGER.error("Failed to commit the changes\n%s", error)
                            return None
                        should_push = True
        if should_push:
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                ["git", "push", "origin", workflow_run.head_branch],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                _LOGGER.error("Failed to push the changes\n%s\n%s", proc.stdout, proc.stderr)
        return None

    def get_json_schema(self) -> module.JsonDict:
        """Get the JSON schema of the module configuration."""
        return {}

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "write",
                "workflows": "read",
            },
            {"workflow_run"},
        )
