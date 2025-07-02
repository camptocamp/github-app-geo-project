"""Utility functions for the auto* modules."""

import asyncio
import io
import logging
import subprocess  # nosec
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import githubkit.webhooks

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class PatchError(Exception):
    """Error while applying the patch."""


def format_process_output(output: subprocess.CompletedProcess[str]) -> str:
    """Format the output of the process."""
    return format_process_out(output.stdout, output.stderr)


def format_process_bytes(stdout: bytes | None, stderr: bytes | None) -> str:
    """Format the output of the process."""
    return format_process_out(
        stdout.decode() if stdout else None,
        stderr.decode() if stderr else None,
    )


def format_process_out(stdout: str | None, stderr: str | None) -> str:
    """Format the output of the process."""
    if stdout and stderr:
        return f"\n{stdout}\nError:\n{stderr}"
    if stdout:
        return f"\n{stdout}"
    if stderr:
        return f"\n{stderr}"
    return ""


class Patch(module.Module[dict[str, Any], dict[str, Any], dict[str, Any], Any]):
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

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.module_event_name == "workflow_run":
            event_data = githubkit.webhooks.parse_obj(
                "workflow_run",
                context.github_event_data,
            )
            if (
                event_data.action == "completed"
                and event_data.workflow_run.conclusion == "failure"
                # Don't run on dynamic workflows like CodeQL
                and (event_data.workflow is None or not event_data.workflow.path.startswith("dynamic/"))
            ):
                return [module.Action(priority=module.PRIORITY_STANDARD, data={})]
        return []

    async def process(
        self,
        context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        assert context.module_event_name == "workflow_run"
        event_data = githubkit.webhooks.parse_obj(
            "workflow_run",
            context.github_event_data,
        )

        # Get workflow artifacts
        artifacts_response = (
            await context.github_project.aio_github.rest.actions.async_list_workflow_run_artifacts(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                run_id=event_data.workflow_run.id,
            )
        )
        artifacts = artifacts_response.parsed_data.artifacts

        if not artifacts:
            _LOGGER.debug("No artifacts found")
            return module.ProcessOutput()

        is_clone = (
            event_data.workflow_run.head_repository.owner.login
            != event_data.workflow_run.repository.owner.login
            if event_data.workflow_run.head_repository.owner and event_data.workflow_run.repository.owner
            else False
        )
        should_push = False
        result_message = []
        error_messages = []

        # Get workflow run details to access head_branch
        head_branch = event_data.workflow_run.head_branch

        with tempfile.TemporaryDirectory() as tmpdirname:
            cwd = Path(tmpdirname)
            if not is_clone:
                new_cwd = await module_utils.git_clone(
                    context.github_project,
                    head_branch,
                    cwd,
                )
                if new_cwd is None:
                    return module.ProcessOutput(
                        success=False,
                        output={
                            "summary": "Failed to clone the repository, see details on the application for details (link below)",
                        },
                    )
                cwd = new_cwd

            for artifact in artifacts:
                if not artifact.name.endswith(".patch"):
                    continue

                if artifact.expired:
                    _LOGGER.info("Artifact %s is expired", artifact.name)
                    continue

                # Get a download URL for the artifact
                download_response = (
                    await context.github_project.aio_github.rest.actions.async_download_artifact(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        artifact_id=artifact.id,
                        archive_format="zip",
                    )
                )

                # Response includes Location header if redirected
                status = download_response.status_code
                if status != 200:
                    _LOGGER.error(
                        "Failed to download artifact %s, status: %s",
                        artifact.name,
                        status,
                    )
                    continue

                # unzip
                with zipfile.ZipFile(io.BytesIO(download_response.content)) as diff:
                    if len(diff.namelist()) != 1:
                        _LOGGER.info("Invalid artifact %s", artifact.name)
                        continue

                    with diff.open(diff.namelist()[0]) as file:
                        patch_input = file.read().decode("utf-8")
                        if not patch_input.strip():
                            _LOGGER.info("Empty patch input in artifact %s", artifact.name)
                            continue
                        message: module_utils.Message = module_utils.HtmlMessage(
                            patch_input,
                            "Applied the patch input",
                        )
                        _LOGGER.debug(message)
                        if is_clone:
                            result_message.extend(["```diff", patch_input, "```"])
                        else:
                            command = ["git", "apply", "--allow-empty", "--verbose"]
                            proc = await asyncio.create_subprocess_exec(
                                *command,
                                stdin=asyncio.subprocess.PIPE,
                                stdout=asyncio.subprocess.PIPE,
                                cwd=cwd,
                            )
                            async with asyncio.timeout(60):
                                stdout, stderr = await proc.communicate(
                                    patch_input.encode(),
                                )
                            message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                command,
                                proc,
                                stdout,
                                stderr,
                            )
                            if proc.returncode != 0:
                                message.title = f"Failed to apply the diff {artifact.name}"
                                _LOGGER.warning(message)
                                error_messages.append(
                                    f"Failed to apply the diff '{artifact.name}', you should probably rebase your branch",
                                )
                                continue

                            message.title = f"Applied the diff {artifact.name}"
                            _LOGGER.info(message)

                            if await module_utils.has_changes(cwd):
                                success = await module_utils.create_commit(
                                    f"{artifact.name[:-6]}\n\nFrom the artifact of the previous workflow run",
                                    cwd,
                                )
                                if not success:
                                    exception_message = "Failed to commit the changes, see logs for details"
                                    raise PatchError(exception_message)
                                should_push = True
            if should_push:
                command = ["git", "push", "origin", f"HEAD:{head_branch}"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                message = module_utils.AnsiProcessMessage.from_async_artifacts(
                    command,
                    proc,
                    stdout,
                    stderr,
                )
                if proc.returncode != 0:
                    message.title = "Failed to push the changes"
                    _LOGGER.warning(message)
                    return module.ProcessOutput(
                        success=False,
                        output={
                            "summary": f"Failed to push the changes{format_process_bytes(stdout, stderr)}",
                        },
                    )
                message.title = "Pushed the changes"
                _LOGGER.debug(message)
        if is_clone and result_message:
            return module.ProcessOutput(
                success=False,
                output={
                    "summary": "\n".join(
                        [*error_messages, "", "Patch to be applied", *result_message],
                    ),
                },
            )
        if error_messages:
            return module.ProcessOutput(
                success=False,
                output={"summary": "\n".join(error_messages)},
            )
        return module.ProcessOutput()

    async def get_json_schema(self) -> dict[str, Any]:
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
