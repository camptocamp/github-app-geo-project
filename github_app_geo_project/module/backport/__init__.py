"""Module to display the status of the workflows in the transversal dashboard."""

import asyncio
import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import aiofiles
import githubkit.exception
import githubkit.versions.latest.models
import githubkit.versions.v2022_11_28.webhooks
import githubkit.versions.v2022_11_28.webhooks.pull_request
import githubkit.webhooks
import security_md
from pydantic import BaseModel

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils

from . import configuration

_LOGGER = logging.getLogger(__name__)

_BRANCH_PREFIX = "ghci/backport/"
_BRANCH_PREFIXES = [
    "ghci/backport/",
    "backport/",
]


class _ActionData(BaseModel):
    type: str
    pull_request_number: int | None = None
    branch: str | None = None


class Backport(module.Module[configuration.BackportConfiguration, _ActionData, None, None]):
    """Module used to backport a pull request to an other branch."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Backport pull request"

    def description(self) -> str:
        """Get the description of the module."""
        return "Backport a pull request to an other branch"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Backport"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "write",
            },
            {"pull_request", "push"},
        )

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        with (Path(__file__).parent / "schema.json").open(encoding="utf-8") as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("backport")  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_ActionData]]:
        """Get the action related to the module and the event."""

        if context.event_name == "pull_request":
            event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)

            # SECURITY.md update
            if (
                event_data_pull_request.action in ("opened", "reopened", "synchronize")
                and "pull_request" in context.event_data
            ):
                for prefix in _BRANCH_PREFIXES:
                    if event_data_pull_request.pull_request.head.ref.startswith(prefix):
                        return [
                            module.Action(
                                _ActionData(
                                    type="check",
                                    branch=event_data_pull_request.pull_request.head.ref,
                                ),
                                checks=True,
                                priority=module.PRIORITY_STATUS,
                                title="Check",
                            ),
                        ]
            actions = (
                [
                    module.Action(
                        _ActionData(type="SECURITY.md"),
                        priority=module.PRIORITY_CRON,
                        title="SECURITY.md",
                    ),
                ]
                if event_data_pull_request.action == "closed"
                else []
            )

            if event_data_pull_request.action == "closed" and event_data_pull_request.pull_request.merged:
                actions.append(
                    module.Action(
                        _ActionData(type="backport"),
                        priority=module.PRIORITY_STANDARD,
                        title="Main",
                    ),
                )
            if event_data_pull_request.action == "labeled" and event_data_pull_request.pull_request.merged:
                actions.append(
                    module.Action(
                        _ActionData(type="backport"),
                        priority=module.PRIORITY_STANDARD,
                        title="Main",
                    ),
                )
            return actions
        if context.event_name == "push":
            event_data_push = githubkit.webhooks.parse_obj("push", context.event_data)
            for commit in event_data_push.commits:
                if "SECURITY.md" in [
                    *(commit.modified or []),
                    *(commit.added or []),
                    *(commit.removed or []),
                ]:
                    return [
                        module.Action(
                            _ActionData(
                                type="SECURITY.md",
                                branch="/".join(event_data_push.ref.split("/")[2:]),
                            ),
                            priority=module.PRIORITY_CRON,
                            title="SECURITY.md",
                        ),
                    ]
        return []

    async def process(
        self,
        context: module.ProcessContext[configuration.BackportConfiguration, _ActionData],
    ) -> module.ProcessOutput[_ActionData, None]:
        """Process the action."""
        if context.module_event_data.type == "check":
            event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)
            # get the BACKPORT_TODO file
            if event_data_pull_request.action in ("opened", "reopened", "synchronize"):
                try:
                    branch = context.module_event_data.branch
                    assert branch is not None
                    backport_todo = (
                        await context.github_project.aio_github.rest.repos.async_get_content(
                            owner=context.github_project.owner,
                            repo=context.github_project.repository,
                            ref=branch,
                            path="BACKPORT_TODO",
                        )
                    ).parsed_data
                    assert isinstance(backport_todo, githubkit.versions.latest.models.ContentFile)
                    assert backport_todo.content is not None
                    return module.ProcessOutput(
                        success=False,
                        output={
                            "summary": "BACKPORT_TODO file found",
                            "text": "There is a BACKPORT_TODO file in the branch, he should be threaded and removed\n\n"
                            + base64.b64decode(backport_todo.content).decode("utf-8"),
                        },
                    )

                except githubkit.exception.RequestFailed as exception:
                    if exception.response.status_code == 404:
                        return module.ProcessOutput()
                    _LOGGER.exception("Error while getting BACKPORT_TODO file")
                    return module.ProcessOutput(
                        success=False,
                        output={
                            "summary": "BACKPORT_TODO error",
                            "text": "Error while getting BACKPORT_TODO file",
                        },
                    )
        elif context.module_event_data.type == "SECURITY.md":
            has_security_md = True
            branch = context.module_event_data.branch
            if branch is None:
                event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)
                has_security_md = False
                if isinstance(
                    event_data_pull_request,
                    githubkit.versions.v2022_11_28.webhooks.pull_request.WebhookPullRequestClosed,  # type: ignore[attr-defined]
                ):
                    commits = (
                        await context.github_project.aio_github.rest.pulls.async_list_commits(
                            context.github_project.owner,
                            context.github_project.repository,
                            event_data_pull_request.number,
                        )
                    ).parsed_data
                    for commit in commits:  # type: ignore[attr-defined]
                        if "SECURITY.md" in [file.filename for file in commit.files or []]:
                            has_security_md = True
                            break
                    branch = event_data_pull_request.pull_request.base.ref

            if has_security_md:
                default_branch = await context.github_project.default_branch()

                if branch == default_branch:
                    try:
                        security_file = (
                            await context.github_project.aio_github.rest.repos.async_get_content(
                                owner=context.github_project.owner,
                                repo=context.github_project.repository,
                                path="SECURITY.md",
                            )
                        ).parsed_data
                        assert isinstance(security_file, githubkit.versions.latest.models.ContentFile)
                        assert security_file.content is not None
                        security = security_md.Security(
                            base64.b64decode(security_file.content).decode("utf-8"),
                        )
                        branches = {*security.branches()}
                    except githubkit.exception.RequestFailed as exception:
                        if exception.response.status_code == 404:
                            _LOGGER.debug("No SECURITY.md file in the repository")
                            branches = set()
                        else:
                            _LOGGER.exception("Error while getting SECURITY.md")
                            raise

                    if branches:
                        branches.add(default_branch)

                    labels_config = context.module_config.get("labels", {})
                    if labels_config.get("auto-delete", configuration.AUTO_DELETE_DEFAULT):
                        # Get labels
                        labels = (
                            await context.github_project.aio_github.rest.issues.async_list_labels_for_repo(
                                owner=context.github_project.owner,
                                repo=context.github_project.repository,
                            )
                        ).parsed_data
                        assert isinstance(labels, list)
                        labels_list: list[githubkit.versions.latest.models.Label] = list(labels)

                        for label in labels_list:
                            if label.name.startswith("backport "):
                                branch = label.name[len("backport ") :]
                                if branch not in branches:
                                    # Delete label
                                    await context.github_project.aio_github.rest.issues.async_delete_label(
                                        owner=context.github_project.owner,
                                        repo=context.github_project.repository,
                                        name=label.name,
                                    )

                    if labels_config.get("auto-create", configuration.AUTO_CREATE_DEFAULT):
                        for branch in branches:
                            try:
                                await context.github_project.aio_github.rest.issues.async_get_label(
                                    owner=context.github_project.owner,
                                    repo=context.github_project.repository,
                                    name=f"backport {branch}",
                                )
                            except githubkit.exception.RequestFailed as e:
                                if e.response.status_code == 404:
                                    # Create the label if it doesn't exist
                                    color = labels_config.get("color", configuration.COLOR_DEFAULT)
                                    color = color.removeprefix("#")
                                    await context.github_project.aio_github.rest.issues.async_create_label(
                                        owner=context.github_project.owner,
                                        repo=context.github_project.repository,
                                        name=f"backport {branch}",
                                        color=color,
                                        description=f"Add this label to backport the pull request to the '{branch}' branch",
                                    )
                                else:
                                    _LOGGER.exception(
                                        "Failed to process label for branch '%s' in repository '%s/%s'.",
                                        branch,
                                        context.github_project.owner,
                                        context.github_project.repository,
                                    )
                                    raise

            return module.ProcessOutput()
        elif context.module_event_data.type == "backport":
            event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)
            pull_request = event_data_pull_request.pull_request
            if event_data_pull_request.action in ("closed", "labeled") and pull_request.state == "closed":
                branches = set()
                for current_label in pull_request.labels:
                    if current_label.name.startswith("backport "):
                        branches.add(current_label.name[len("backport ") :])

                if branches:
                    _LOGGER.debug("Branches: %s", ", ".join(branches))
                else:
                    _LOGGER.debug("No branches to backport")

                return module.ProcessOutput(
                    actions=[
                        module.Action(
                            _ActionData(
                                type="version",
                                pull_request_number=pull_request.number,
                                branch=branch,
                            ),
                            priority=module.PRIORITY_STANDARD,
                            title=f"{branch}",
                        )
                        for branch in branches
                    ],
                )
            return module.ProcessOutput()

        if context.module_event_data.type == "version":
            event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", context.event_data)
            assert context.module_event_data.pull_request_number is not None
            pull_request = event_data_pull_request.pull_request
            assert context.module_event_data.branch is not None
            if await self._backport(
                context,
                event_data_pull_request,
                context.module_event_data.branch,
            ):
                return module.ProcessOutput(
                    output={
                        "summary": "Backport pull request created",
                    },
                )
            return module.ProcessOutput(
                success=False,
                output={
                    "summary": "Error while backporting the pull request",
                },
            )

        return module.ProcessOutput()

    async def _backport(
        self,
        context: module.ProcessContext[configuration.BackportConfiguration, _ActionData],
        event_data_pull_request: githubkit.versions.v2022_11_28.webhooks.PullRequestEvent,
        target_branch: str,
    ) -> bool:
        """Backport the pull request to the target branch."""
        pull_request = event_data_pull_request.pull_request
        backport_branch = f"{_BRANCH_PREFIX}{pull_request.number}-to-{target_branch}"
        try:
            # Check if branch exists
            await context.github_project.aio_github.rest.repos.async_get_branch(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                branch=backport_branch,
            )
            _LOGGER.error("Branch %s already exists", backport_branch)
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code != 404:
                _LOGGER.exception("Error while getting branch %s", backport_branch)
                raise
        else:
            return False

        # Checkout the right branch on a temporary directory
        with tempfile.TemporaryDirectory() as tmpdirname:
            cwd = Path(tmpdirname)
            _LOGGER.debug("Clone the repository in the temporary directory: %s", tmpdirname)
            new_cwd = await module_utils.git_clone(context.github_project, target_branch, cwd)
            if new_cwd is None:
                _LOGGER.error(
                    "Error on cloning the repository %s/%s",
                    context.github_project.owner,
                    context.github_project.repository,
                )
                return False
            cwd = new_cwd

            # Get the branches
            command = ["git", "branch", "-a"]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            async with asyncio.timeout(60):
                stdout, stderr = await proc.communicate()
            ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                command,
                proc,
                stdout,
                stderr,
            )
            ansi_message.title = "List of the branches"
            _LOGGER.debug(ansi_message)
            branches = stdout.decode().splitlines()
            _LOGGER.debug("Branches: %s", ", ".join(branches))

            # Checkout the branch
            command = ["git", "checkout", "-b", backport_branch, f"origin/{target_branch}"]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            async with asyncio.timeout(60):
                stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                    command,
                    proc,
                    stdout,
                    stderr,
                )
                ansi_message.title = f"Error while creating the branch {backport_branch}"
                _LOGGER.error(ansi_message)
                raise module.GHCIError(ansi_message.title)

            failed_commits: list[str] = []

            # Get pull request commits
            pull_request_commits = (
                await context.github_project.aio_github.rest.pulls.async_list_commits(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    pull_number=pull_request.number,
                )
            ).parsed_data
            commits: list[
                githubkit.versions.latest.models.Commit | githubkit.versions.latest.models.GitCommit
            ] = list(pull_request_commits)

            if len(commits) != 1:
                merge_commit_sha = event_data_pull_request.pull_request.merge_commit_sha
                if merge_commit_sha:
                    # Get merge commit
                    merge_commit = (
                        await context.github_project.aio_github.rest.git.async_get_commit(
                            owner=context.github_project.owner,
                            repo=context.github_project.repository,
                            commit_sha=merge_commit_sha,
                        )
                    ).parsed_data

                    # Check if the pull request is a squash merge commit
                    if len(merge_commit.parents) == 1:
                        commits = [merge_commit]

            # For all commits in the pull request
            for commit in commits:
                # Cherry-pick the commit
                if failed_commits:
                    failed_commits.append(commit.sha)
                else:
                    try:
                        command = ["git", "fetch", "origin", commit.sha]
                        proc = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=cwd,
                        )
                        async with asyncio.timeout(300):
                            stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                command,
                                proc,
                                stdout,
                                stderr,
                            )
                            ansi_message.title = f"Error while fetching {commit.sha}"
                            _LOGGER.error(ansi_message)
                            failed_commits.append(commit.sha)
                            continue

                        # Get user email
                        command = ["git", "--no-pager", "log", "--format=format:%ae", "-n", "1", commit.sha]
                        proc = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=cwd,
                        )
                        async with asyncio.timeout(60):
                            stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                command,
                                proc,
                                stdout,
                                stderr,
                            )
                            ansi_message.title = f"Error while getting user email {commit.sha}"
                            _LOGGER.error(ansi_message)
                        else:
                            user_email = stdout.decode().strip()
                            # Set the user email
                            command = ["git", "config", "--global", "user.email", user_email]
                            proc = await asyncio.create_subprocess_exec(
                                *command,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                cwd=cwd,
                            )
                            async with asyncio.timeout(10):
                                stdout, stderr = await proc.communicate()
                            if proc.returncode != 0:
                                ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                    command,
                                    proc,
                                    stdout,
                                    stderr,
                                )
                                ansi_message.title = f"Error while setting user email {user_email}"
                                _LOGGER.error(ansi_message)

                        # Get user name
                        command = ["git", "--no-pager", "log", "--format=format:%an", "-n", "1", commit.sha]
                        proc = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=cwd,
                        )
                        async with asyncio.timeout(60):
                            stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                command,
                                proc,
                                stdout,
                                stderr,
                            )
                            ansi_message.title = f"Error while getting user name {commit.sha}"
                            _LOGGER.error(ansi_message)
                        else:
                            user_name = stdout.decode().strip()
                            # Set the user name
                            command = ["git", "config", "--global", "user.name", user_name]
                            proc = await asyncio.create_subprocess_exec(
                                *command,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                cwd=cwd,
                            )
                            async with asyncio.timeout(10):
                                stdout, stderr = await proc.communicate()
                            if proc.returncode != 0:
                                ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                    command,
                                    proc,
                                    stdout,
                                    stderr,
                                )
                                ansi_message.title = f"Error while setting user name {user_name}"
                                _LOGGER.error(ansi_message)

                        command = ["git", "cherry-pick", commit.sha]
                        proc = await asyncio.create_subprocess_exec(
                            *command,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=cwd,
                        )
                        async with asyncio.timeout(60):
                            stdout, stderr = await proc.communicate()
                        if proc.returncode != 0:
                            ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                command,
                                proc,
                                stdout,
                                stderr,
                            )
                            ansi_message.title = f"Error while cherry-picking {commit.sha}"
                            _LOGGER.error(ansi_message)
                            failed_commits.append(commit.sha)

                            command = ["git", "cherry-pick", "--abort"]
                            proc = await asyncio.create_subprocess_exec(
                                *command,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                                cwd=cwd,
                            )
                            async with asyncio.timeout(10):
                                stdout, stderr = await proc.communicate()
                            if proc.returncode != 0:
                                ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                                    command,
                                    proc,
                                    stdout,
                                    stderr,
                                )
                                ansi_message.title = f"Error while aborting the cherry-pick {commit.sha}"
                                _LOGGER.error(ansi_message)
                    except module.GHCIError:
                        failed_commits.append(commit.sha)

            message = [f"Backport of #{pull_request.number} to {target_branch}"]
            if failed_commits:
                message.extend(
                    [
                        "",
                        f"Error on cherry-picking: {', '.join(failed_commits)}",
                        "",
                        "To continue do:",
                        "```bash",
                        "git fetch && \\",
                        f"  git checkout {backport_branch} && \\",
                        "  git reset --hard HEAD^ && \\",
                        f"  git cherry-pick {' '.join(failed_commits)}",
                        f"git push origin {backport_branch} --force",
                        "```",
                    ],
                )
                async with aiofiles.open(cwd / "BACKPORT_TODO", "w", encoding="utf-8") as f:
                    await f.write("\n".join(message))
                command = ["git", "add", "BACKPORT_TODO"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(10):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                        command,
                        proc,
                        stdout,
                        stderr,
                    )
                    ansi_message.title = "Error while adding the BACKPORT_TODO file"
                    _LOGGER.error(ansi_message)
                    raise module.GHCIError(ansi_message.title)
                command = ["git", "commit", "--message=[skip ci] Add instructions to finish the backport"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(10):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    ansi_message = module_utils.AnsiProcessMessage.from_async_artifacts(
                        command,
                        proc,
                        stdout,
                        stderr,
                    )
                    ansi_message.title = "Error while committing the BACKPORT_TODO file"
                    _LOGGER.error(ansi_message)
                    raise module.GHCIError(ansi_message.title)
            await module_utils.create_pull_request(
                target_branch,
                backport_branch,
                f"[Backport {target_branch}] {pull_request.title}",
                "\n".join(message),
                github_project=context.github_project,
                cwd=cwd,
                auto_merge=False,
            )
            # Remove backport label
            try:
                await context.github_project.aio_github.rest.issues.async_remove_label(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    issue_number=pull_request.number,
                    name=f"backport {target_branch}",
                )
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code != 404:
                    _LOGGER.exception("Error while removing label backport %s", target_branch)
                    raise
        return True
