# Copyright (c) 2026, Camptocamp SA

"""Utility functions for the auto* modules."""

import asyncio
import io
import logging
import re
import subprocess  # nosec
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import githubkit.exception
import githubkit.webhooks
import githubkit_schemas.latest.models
from pydantic import BaseModel

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)
_CODEQL_JOB_NAME_MATCHER = re.compile(r"^Analyze \([a-z]+\)$")
_PATCH_COMMIT_TRAILER = "From the artifact of the previous workflow run"
_PATCH_BRANCH_PREFIX = "ghci/patch/"
_BRANCH_SUFFIX_MATCHER = re.compile(r"^(?P<message>.+) \[(?P<branch>[^\[\]]+)\]$")


class PatchError(Exception):
    """Error while applying the patch."""


class _PatchTarget(BaseModel):
    """Where the patch of an artifact must be applied."""

    message: str
    branch: str | None = None
    error: str | None = None


def _resolve_artifact_target(
    artifact_name: str,
    head_branch: str,
    run_event: str | None,
    branch_override_events: list[str],
) -> _PatchTarget:
    """
    Resolve the commit message and the target branch of a patch artifact.

    The artifact name is `<message>.patch` to apply the patch on the branch of the workflow run, or
    `<message> [<branch>].patch` to apply it on another branch. The branch override is only honoured for the
    workflow run events listed in `branch_override_events`: a patch produced by a pull request workflow must
    never be applied on another branch than the pull request one.
    """
    message = artifact_name.removesuffix(".patch")
    match = _BRANCH_SUFFIX_MATCHER.match(message)
    if match is None:
        return _PatchTarget(message=message, branch=head_branch)
    override_message = match.group("message")
    override_branch = match.group("branch")
    if run_event is None or run_event not in branch_override_events:
        return _PatchTarget(
            message=override_message,
            error=(
                f"The artifact '{artifact_name}' asks to apply its patch on the branch '{override_branch}', "
                f"but the workflow run event '{run_event}' is not allowed to target another branch "
                f"(allowed events: {', '.join(sorted(branch_override_events))})"
            ),
        )
    return _PatchTarget(message=override_message, branch=override_branch)


async def _count_consecutive_patch_commits(
    context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    head_branch: str,
    limit: int,
) -> int:
    """Count the consecutive commits created by the patch module at the head of the branch, up to `limit`."""
    if limit <= 0:
        return 0
    commits = (
        await context.github_project.aio_github.rest.repos.async_list_commits(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            sha=head_branch,
            per_page=limit,
        )
    ).parsed_data
    count = 0
    for commit in commits or []:
        if commit.commit is not None and _PATCH_COMMIT_TRAILER in commit.commit.message:
            count += 1
        else:
            break
    return count


async def _list_open_patch_pull_requests(
    context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    head_branch: str,
    limit: int,
) -> list[githubkit_schemas.latest.models.PullRequest]:
    """List the open pull requests created by the patch module for the branch, up to `limit`."""
    if limit <= 0:
        return []
    prefix = f"{_PATCH_BRANCH_PREFIX}{head_branch}-"
    patch_pull_requests: list[githubkit_schemas.latest.models.PullRequest] = []
    per_page = 100
    page = 1
    while len(patch_pull_requests) < limit:
        pull_requests = (
            await context.github_project.aio_github.rest.pulls.async_list(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                state="open",
                per_page=per_page,
                page=page,
            )
        ).parsed_data
        if not pull_requests:
            break
        assert pull_requests is not None
        for pull_request in pull_requests:
            if pull_request.head is not None and pull_request.head.ref.startswith(prefix):
                patch_pull_requests.append(pull_request)
                if len(patch_pull_requests) >= limit:
                    break
        if len(pull_requests) < per_page:
            # Last page, no need to ask for the next one.
            break
        page += 1
    return patch_pull_requests


async def _find_reusable_patch_pull_request(
    context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    pull_requests: list[githubkit_schemas.latest.models.PullRequest],
) -> githubkit_schemas.latest.models.PullRequest | None:
    """
    Find an open patch pull request that can be updated in place of creating a new one.

    Only a pull request whose head commit was created by the patch module is reusable, to never force push
    over the commits added by a human contributor. This avoids opening a new pull request on every run of a
    scheduled workflow whose patch is not merged yet.
    """

    async def _is_patch_commit(pull_request: githubkit_schemas.latest.models.PullRequest) -> bool:
        if pull_request.head is None or pull_request.head.sha is None:
            return False
        try:
            commit = (
                await context.github_project.aio_github.rest.repos.async_get_commit(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    ref=pull_request.head.sha,
                )
            ).parsed_data
        except githubkit.exception.RequestFailed:
            # An unreadable head commit only disqualifies that pull request, not the whole patch application.
            _LOGGER.exception("Failed to get the commit %s", pull_request.head.sha)
            return False
        return (
            commit is not None
            and commit.commit is not None
            and _PATCH_COMMIT_TRAILER in commit.commit.message
        )

    reusable = await asyncio.gather(*[_is_patch_commit(pull_request) for pull_request in pull_requests])
    for pull_request, is_reusable in zip(pull_requests, reusable, strict=True):
        if is_reusable:
            return pull_request
    return None


async def _iter_artifact_patches(
    context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    artifacts: list[Any],
) -> AsyncIterator[tuple[Any, str]]:
    """Iterate over artifacts and yield (artifact, patch_input) for valid patches."""
    for artifact in artifacts:
        if not artifact.name.endswith(".patch"):
            continue

        if artifact.expired:
            _LOGGER.info("Artifact %s is expired", artifact.name)
            continue

        download_response = await context.github_project.aio_github.rest.actions.async_download_artifact(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            artifact_id=artifact.id,
            archive_format="zip",
        )

        status = download_response.status_code
        if status != 200:
            _LOGGER.error(
                "Failed to download artifact %s, status: %s",
                artifact.name,
                status,
            )
            continue

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
                yield artifact, patch_input


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


async def _apply_patches(
    context: module.ProcessContext[dict[str, Any], dict[str, Any]],
    run_id: int,
    target_branch: str,
    artifacts: list[Any],
    messages_by_artifact: dict[str, str],
) -> list[str]:
    """
    Apply the patch artifacts on the target branch.

    Returns the messages to add to the check output summary, an empty list means that everything succeeded.
    """
    # Check if the branch exists before attempting to create a worktree
    try:
        await context.github_project.aio_github.rest.repos.async_get_branch(
            owner=context.github_project.owner,
            repo=context.github_project.repository,
            branch=target_branch,
        )
    except githubkit.exception.RequestFailed as exception:
        if exception.response.status_code == 404:
            _LOGGER.info(
                "Branch '%s' no longer exists — skipping patch application",
                target_branch,
            )
            return []
        raise

    max_consecutive_commits = settings.patch.max_consecutive_commits
    consecutive_patch_commits = await _count_consecutive_patch_commits(
        context,
        target_branch,
        max_consecutive_commits,
    )
    if consecutive_patch_commits >= max_consecutive_commits > 0:
        summary = (
            f"The last {consecutive_patch_commits} commits on the branch '{target_branch}' were "
            f"created by the patch module (limit: {max_consecutive_commits}), "
            "refusing to apply new patches to avoid an infinite loop. "
            "This usually means that the CI fix is unstable (for example a non-deterministic "
            "pre-commit); fix the underlying issue or push a commit manually to reset the counter."
        )
        _LOGGER.warning(summary)
        return [summary]

    should_push = False
    last_message = ""
    error_messages: list[str] = []

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        target_branch,
    ) as cwd:
        async for artifact, patch_input in _iter_artifact_patches(
            context,
            artifacts,
        ):
            command = ["git", "apply", "--allow-empty", "--index", "--verbose"]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            async with asyncio.timeout(settings.patch.timeouts.git_apply.total_seconds()):
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

            if await module_utils.has_changes(cwd, include_un_followed=True):
                commit_message = messages_by_artifact.get(artifact.name, artifact.name.removesuffix(".patch"))
                success = await module_utils.create_commit(
                    f"{commit_message}\n\n{_PATCH_COMMIT_TRAILER}",
                    cwd,
                )
                if not success:
                    exception_message = "Failed to commit the changes, see logs for details"
                    raise PatchError(exception_message)
                should_push = True
                last_message = commit_message
        if should_push:
            command = ["git", "push", "origin", f"HEAD:{target_branch}"]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            async with asyncio.timeout(settings.patch.timeouts.git_push.total_seconds()):
                stdout, stderr = await proc.communicate()
            message = module_utils.AnsiProcessMessage.from_async_artifacts(
                command,
                proc,
                stdout,
                stderr,
            )
            if proc.returncode != 0:
                stderr_text = stderr.decode() if stderr else ""
                if "protected branch hook declined" in stderr_text:
                    open_patch_pull_requests = await _list_open_patch_pull_requests(
                        context,
                        target_branch,
                        max_consecutive_commits,
                    )
                    if len(open_patch_pull_requests) >= max_consecutive_commits > 0:
                        summary = (
                            f"There are already {len(open_patch_pull_requests)} open "
                            f"'{_PATCH_BRANCH_PREFIX}{target_branch}-*' pull requests created by the "
                            f"patch module (limit: {max_consecutive_commits}), refusing to create "
                            "a new one to avoid an infinite loop. "
                            "This usually means that the CI fix is unstable (for example a "
                            "non-deterministic pre-commit); fix the underlying issue or close "
                            "the pending pull requests."
                        )
                        _LOGGER.warning(summary)
                        return [*error_messages, summary]
                    reusable_pull_request = await _find_reusable_patch_pull_request(
                        context,
                        open_patch_pull_requests,
                    )
                    if reusable_pull_request is not None and reusable_pull_request.head is not None:
                        new_branch = reusable_pull_request.head.ref
                        _LOGGER.info(
                            "Branch '%s' is protected, updating the pull request #%s instead of creating a new one",
                            target_branch,
                            reusable_pull_request.number,
                        )
                    else:
                        new_branch = f"{_PATCH_BRANCH_PREFIX}{target_branch}-{run_id}"
                        _LOGGER.info(
                            "Branch '%s' is protected, creating a pull request instead",
                            target_branch,
                        )
                    pr_title = last_message
                    pr_body = (
                        f"Automated patch from workflow run "
                        f"[{run_id}](https://github.com/{context.github_project.owner}"
                        f"/{context.github_project.repository}/actions/runs/{run_id})"
                    )
                    pr_success, pull_request = await module_utils.create_pull_request(
                        target_branch,
                        new_branch,
                        pr_title,
                        pr_body,
                        context.github_project,
                        cwd,
                    )
                    if not pr_success:
                        message.title = "Failed to create pull request after protected branch push rejection"
                        _LOGGER.warning(message)
                        return [
                            *error_messages,
                            "Failed to create pull request after protected branch push rejection",
                        ]
                    if pull_request is not None:
                        _LOGGER.info("Created or updated the pull request %s", pull_request.html_url)
                else:
                    message.title = "Failed to push the changes"
                    _LOGGER.warning(message)
                    return [*error_messages, "Failed to push the changes"]
            else:
                message.title = "Pushed the changes"
                _LOGGER.debug(message)
    return error_messages


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
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/patch/README.md"

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.module_event_name == "workflow_job":
            event_data_workflow_job = githubkit.webhooks.parse_obj(
                "workflow_job",
                context.github_event_data,
            )

            _LOGGER.debug(
                """Received workflow job with user information (will be used to check if the user is trusted):
                actor: %s,
                sender type: %s.""",
                event_data_workflow_job.workflow_job.run_attempt,
                event_data_workflow_job.sender.type,
            )

            if (
                event_data_workflow_job.action == "completed"
                and event_data_workflow_job.workflow_job.conclusion == "failure"
                # Don't run on dynamic workflows like CodeQL
                and not _CODEQL_JOB_NAME_MATCHER.match(event_data_workflow_job.workflow_job.name)
            ):
                return [module.Action(priority=module.PRIORITY_STANDARD, data={})]
        if context.module_event_name == "workflow_run":
            event_data_workflow_run = githubkit.webhooks.parse_obj(
                "workflow_run",
                context.github_event_data,
            )

            _LOGGER.debug(
                """Received workflow job with user information (will be used to check if the user is trusted):
                actor: %s,
                sender: %s,
                triggering_actor: %s.""",
                event_data_workflow_run.workflow_run.actor,
                event_data_workflow_run.sender.login,
                event_data_workflow_run.workflow_run.triggering_actor,
            )

            if (
                event_data_workflow_run.action == "completed"
                and event_data_workflow_run.workflow_run.conclusion == "failure"
                # Don't run on dynamic workflows like CodeQL
                and (
                    event_data_workflow_run.workflow is None
                    or not event_data_workflow_run.workflow.path.startswith("dynamic/")
                )
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
        if context.module_event_name == "workflow_job":
            event_data_workflow_job = githubkit.webhooks.parse_obj(
                "workflow_job",
                context.github_event_data,
            )
            run_id = event_data_workflow_job.workflow_job.run_id
            head_branch = event_data_workflow_job.workflow_job.head_branch
            is_clone = False
            run_event: str | None = None
            try:
                workflow_run_response = (
                    await context.github_project.aio_github.rest.actions.async_get_workflow_run(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        run_id=run_id,
                    )
                )
                workflow_run = workflow_run_response.parsed_data
                run_event = workflow_run.event if workflow_run is not None else None
                head_repo = getattr(workflow_run, "head_repository", None)
                base_repo = getattr(workflow_run, "repository", None)
                head_owner = getattr(head_repo, "owner", None) if head_repo is not None else None
                base_owner = getattr(base_repo, "owner", None) if base_repo is not None else None
                is_clone = (
                    getattr(head_owner, "login", None) != getattr(base_owner, "login", None)
                    if head_owner is not None and base_owner is not None
                    else False
                )
            except githubkit.exception.RequestFailed as exception:
                # If we cannot determine fork information, fall back to assuming it's not a clone.
                _LOGGER.exception(
                    "Failed to get workflow run information for run_id %s: %s",
                    run_id,
                    exception.response.status_code,
                )
        elif context.module_event_name == "workflow_run":
            event_data_workflow_run = githubkit.webhooks.parse_obj(
                "workflow_run",
                context.github_event_data,
            )
            run_id = event_data_workflow_run.workflow_run.id
            head_branch = event_data_workflow_run.workflow_run.head_branch
            run_event = event_data_workflow_run.workflow_run.event
            is_clone = (
                event_data_workflow_run.workflow_run.head_repository.owner.login
                != event_data_workflow_run.workflow_run.repository.owner.login
                if event_data_workflow_run.workflow_run.head_repository.owner
                and event_data_workflow_run.workflow_run.repository.owner
                else False
            )
        else:
            error_message = f"Invalid event '{context.module_event_name}' for the Patch module"
            raise PatchError(error_message)

        if head_branch is None:
            _LOGGER.error("workflow event head_branch is None; cannot apply patch.")
            error_message = "Missing head branch information from workflow event"
            raise PatchError(error_message)

        # Get workflow artifacts
        artifacts_response = (
            await context.github_project.aio_github.rest.actions.async_list_workflow_run_artifacts(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                run_id=run_id,
            )
        )
        artifacts = artifacts_response.parsed_data.artifacts

        if not artifacts:
            _LOGGER.debug("No artifacts found")
            return module.ProcessOutput()

        artifacts.sort(
            key=lambda artifact: (
                artifact.created_at.timestamp() if artifact.created_at is not None else float("-inf")
            ),
        )

        result_message: list[str] = []
        error_messages: list[str] = []

        if is_clone:
            async for _artifact, patch_input in _iter_artifact_patches(
                context,
                artifacts,
            ):
                result_message.extend(["```diff", patch_input, "```"])
            if result_message:
                return module.ProcessOutput(
                    success=False,
                    check_output={
                        "summary": "\n".join(
                            ["", "Patch to be applied", *result_message],
                        ),
                    },
                )
            return module.ProcessOutput()

        artifacts_by_branch: dict[str, list[Any]] = {}
        messages_by_artifact: dict[str, str] = {}
        for artifact in artifacts:
            if not artifact.name.endswith(".patch"):
                continue
            target = _resolve_artifact_target(
                artifact.name,
                head_branch,
                run_event,
                settings.patch.branch_override_events,
            )
            if target.branch is None:
                assert target.error is not None
                _LOGGER.warning(target.error)
                error_messages.append(target.error)
                continue
            artifacts_by_branch.setdefault(target.branch, []).append(artifact)
            messages_by_artifact[artifact.name] = target.message

        for target_branch, branch_artifacts in artifacts_by_branch.items():
            error_messages.extend(
                await _apply_patches(
                    context,
                    run_id,
                    target_branch,
                    branch_artifacts,
                    messages_by_artifact,
                )
            )

        if error_messages:
            return module.ProcessOutput(
                success=False,
                check_output={"summary": "\n".join(error_messages)},
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
                "pull_requests": "write",
                "workflows": "read",
            },
            {"workflow_run", "workflow_job"},
        )
