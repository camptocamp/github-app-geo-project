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
from github_app_geo_project.module import utils as module_utils

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


class Patch(module.Module[dict[str, Any], dict[str, Any], dict[str, Any]]):
    """Module that apply the patch present in the artifact on the branch of the pull request."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Apply the patch from the artifacts"

    def description(self) -> str:
        """Get the description of the module."""
        return "This module apply the patch present in the artifact on the branch of the pull request."

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Patch"

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if (
            context.event_data.get("action") == "completed"
            and context.event_data.get("workflow_run", {}).get("conclusion") == "failure"
            # Don't run on dynamic workflows like CodeQL
            and not context.event_data.get("workflow", {}).get("path", {}).startswith("dynamic/")
        ):
            return [module.Action(priority=module.PRIORITY_STANDARD, data={})]
        return []

    async def process(
        self, context: module.ProcessContext[dict[str, Any], dict[str, Any], dict[str, Any]]
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        repo = context.github_project.repo
        workflow_run = repo.get_workflow_run(cast(int, context.event_data["workflow_run"]["id"]))
        if not workflow_run.get_artifacts():
            _LOGGER.debug("No artifacts found")
            return module.ProcessOutput()

        is_clone = context.event_data.get("workflow_run", {}).get("head_repository", {}).get("owner", {}).get(
            "login", ""
        ) != context.event_data.get("workflow_run", {}).get("repository", {}).get("owner", {}).get(
            "login", ""
        )
        should_push = False
        result_message = []

        async with module_utils.WORKING_DIRECTORY_LOCK:
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                if not is_clone:
                    success = module_utils.git_clone(context.github_project, workflow_run.head_branch)
                    if not success:
                        return module.ProcessOutput(
                            success=False,
                            output={
                                "summary": "Failed to clone the repository, see details on the application for details (link below)"
                            },
                        )

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
                            patch_input = file.read().decode("utf-8")
                            message: module_utils.Message = module_utils.HtmlMessage(
                                patch_input, "Applied the patch input"
                            )
                            _LOGGER.debug(message)
                            if is_clone:
                                result_message.extend(["```diff", patch_input, "```"])
                            else:
                                proc = subprocess.run(  # pylint: disable=subprocess-run-check
                                    ["git", "apply", "--allow-empty"],
                                    input=patch_input,
                                    encoding="utf-8",
                                    capture_output=True,
                                    timeout=30,
                                )
                                message = module_utils.ansi_proc_message(proc)
                                if proc.returncode != 0:
                                    message.title = f"Failed to apply the diff {artifact.name}"
                                    _LOGGER.warning(message)
                                    if not should_push:
                                        return module.ProcessOutput(
                                            success=False,
                                            output={
                                                "summary": "Failed to apply the diff, you should probably rebase your branch"
                                            },
                                        )
                                    else:
                                        break
                                message.title = f"Applied the diff {artifact.name}"
                                _LOGGER.info(message)

                                if module_utils.has_changes(include_un_followed=True):
                                    success = await module_utils.create_commit(
                                        f"{artifact.name[:-6]}\n\nFrom the artifact of the previous workflow run"
                                    )
                                    if not success:
                                        raise PatchException(
                                            "Failed to commit the changes, see logs for details"
                                        )
                                    should_push = True
                if should_push:
                    proc = subprocess.run(  # pylint: disable=subprocess-run-check
                        ["git", "push", "origin", f"HEAD:{workflow_run.head_branch}"],
                        capture_output=True,
                        encoding="utf-8",
                        timeout=60,
                    )
                    if proc.returncode != 0:
                        raise PatchException(f"Failed to push the changes{format_process_output(proc)}")
                os.chdir("/")
        if is_clone and result_message:
            return module.ProcessOutput(
                success=False,
                output={"summary": "\n".join(["Patch to be applied", *result_message])},
            )
        return module.ProcessOutput()

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
