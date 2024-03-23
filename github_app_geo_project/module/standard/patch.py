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


class PatchException(Exception):
    """Error while applying the patch."""


def format_process_output(output: subprocess.CompletedProcess[str]) -> str:
    """Format the output of the process."""
    if output.stdout and output.stderr:
        return f"\n{output.stdout}\nError:\n{output.stderr}"
    if output.stdout:
        return f"\n{output.stdout}"
    if output.stderr:
        return f"\n{output.stderr}"
    return ""


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
        if (
            context.event_data.get("action") == "completed"
            and context.event_data.get("workflow_run", {}).get("pull_requests")
            and context.event_data.get("workflow_run", {}).get("conclusion") == "failure"
        ):
            return [module.Action(priority=module.PRIORITY_CRON, data={})]
        return []

    def process(self, context: module.ProcessContext[dict[str, Any]]) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repo = context.github_project.github.get_repo(
            f"{context.github_project.owner}/{context.github_project.repository}"
        )
        workflow_run = repo.get_workflow_run(cast(int, context.event_data["workflow_run"]["id"]))
        if not workflow_run.get_artifacts():
            _LOGGER.debug("No artifacts found")
            return None

        token = context.github_project.token
        should_push = False
        with tempfile.TemporaryDirectory() as tmpdirname:
            os.chdir(tmpdirname)
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "git",
                    "clone",
                    "--depth=1",
                    f"--branch={workflow_run.head_branch}",
                    f"https://x-access-token:{token}@github.com/{context.github_project.owner}/{context.github_project.repository}.git",
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                raise PatchException(f"Failed to clone the repository{format_process_output(proc)}")
            os.chdir(context.github_project.repository.split("/")[-1])
            app = context.github_project.application.integration.get_app()
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "git",
                    "config",
                    "--global",
                    "user.email",
                    f"{app.id}+{app.slug}[bot]@users.noreply.github.com",
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                raise PatchException(f"Failed to set the email{format_process_output(proc)}")
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                ["git", "config", "--global", "user.name", app.name],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                raise PatchException(f"Failed to set the name{format_process_output(proc)}")

            for artifact in workflow_run.get_artifacts():
                if not artifact.name.endswith(".patch"):
                    continue

                if artifact.expired:
                    _LOGGER.info("Artifact %s is expired", artifact.name)
                    continue

                (
                    status,
                    headers,
                    response_redirect,
                ) = workflow_run._requester.requestJson(  # pylint: disable=protected-access
                    "GET", artifact.archive_download_url
                )
                if status != 302:
                    _LOGGER.error(
                        "Failed to download artifact %s, status: %s, data:\n%s",
                        artifact.name,
                        status,
                        response_redirect,
                    )
                    continue

                # Follow redirect.
                response = requests.get(headers["location"], timeout=120)
                if not response.ok:
                    _LOGGER.error("Failed to download artifact %s", artifact.name)
                    continue

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
                            raise PatchException(f"Failed to apply the diff{format_process_output(proc)}")
                        error = utils.create_commit(artifact.name[:-5])
                        if error:
                            raise PatchException(f"Failed to commit the changes\n{error}")
                        should_push = True
            if should_push:
                proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                    ["git", "push", "origin", f"HEAD:{workflow_run.head_branch}"],
                    capture_output=True,
                    encoding="utf-8",
                )
                if proc.returncode != 0:
                    raise PatchException(f"Failed to push the changes{format_process_output(proc)}")
        return None

    def get_json_schema(self) -> dict[str, Any]:
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
