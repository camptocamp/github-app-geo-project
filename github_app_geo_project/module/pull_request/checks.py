"""Module to display the status of the workflows in the transversal dashboard."""

import asyncio
import base64
import json
import logging
import re
import tempfile
import typing
from pathlib import Path
from typing import Any, cast

import githubkit
import githubkit.exception
import githubkit.versions.latest.models
import githubkit.versions.v2022_11_28.webhooks
import githubkit.webhooks

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.pull_request import checks_configuration

_LOGGER = logging.getLogger(__name__)

if typing.TYPE_CHECKING:
    NamedTemporaryFileStr = tempfile._TemporaryFileWrapper[str]  # pylint: disable=protected-access
else:
    NamedTemporaryFileStr = tempfile._TemporaryFileWrapper  # pylint: disable=protected-access


async def _get_code_spell_command(
    context: module.ProcessContext[
        checks_configuration.PullRequestChecksConfiguration,
        dict[str, Any],
    ],
    event_data: githubkit.versions.v2022_11_28.webhooks.PullRequestEvent,
    ignore_file: NamedTemporaryFileStr,
) -> list[str]:
    """Get the codespell command."""
    config = context.module_config
    code_spell_config = config.get("codespell", {})
    code_spell_config = code_spell_config if isinstance(code_spell_config, dict) else {}
    command = ["/venv/bin/codespell"]
    for spell_ignore_file in (
        ".github/spell-ignore-words.txt",
        "spell-ignore-words.txt",
        ".spell-ignore-words.txt",
    ):
        try:
            content = (
                await context.github_project.aio_github.rest.repos.async_get_content(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    ref=event_data.pull_request.head.sha,
                    path=spell_ignore_file,
                )
            ).parsed_data
            assert isinstance(content, githubkit.versions.latest.models.ContentFile)
            assert content.content is not None
            ignore_file.write(base64.b64decode(content.content).decode("utf-8"))
            ignore_file.flush()
            command.append(f"--ignore-words={ignore_file.name}")
            break
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code != 404:
                raise

    dictionaries = code_spell_config.get(
        "internal-dictionaries",
        checks_configuration.CODESPELL_DICTIONARIES_DEFAULT,
    )
    if dictionaries:
        command.append("--builtin=" + ",".join(dictionaries))
    command += code_spell_config.get(
        "arguments",
        checks_configuration.CODESPELL_ARGUMENTS_DEFAULT,
    )
    return command


def _commits_messages(
    config: checks_configuration.PullRequestChecksConfiguration,
    commits: list[githubkit.versions.latest.models.Commit],
) -> tuple[bool, list[str]]:
    """
    Check the commits messages.

    - They should start with a capital letter.
    - They should not be too short.
    - They should not be a squash or fixup commit.
    - They should not be a merge commit.
    - They should not be a revert commit.
    """
    messages = []
    commit_message_config = config.get(
        "commits-messages",
        cast("checks_configuration.PullRequestChecksCommitsMessagesConfiguration", {}),
    )
    if commit_message_config is False:
        return True, []
    if commit_message_config is True:
        commit_message_config = {}
    success = True
    first_capital = re.compile(r"^[^a-z]")
    commit_hash = set()
    for commit in commits:
        commit_hash.add(commit.sha)
        message_lines = commit.commit.message.split("\n")
        head = message_lines[0]
        if commit_message_config.get(
            "check-fixup",
            checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIXUP_DEFAULT,
        ) and head.startswith("fixup! "):
            _LOGGER.warning("Fixup message not allowed")
            messages.append(f":x: Fixup message not allowed in commit {commit.sha}")
            success = False
        else:
            messages.append(
                f":heavy_check_mark: The commit {commit.sha} is not a fixup commit",
            )
        if commit_message_config.get(
            "check-squash",
            checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_SQUASH_DEFAULT,
        ) and head.startswith("squash! "):
            _LOGGER.warning("Squash message not allowed")
            messages.append(f":x: Squash message not allowed in commit {commit.sha}")
            success = False
        else:
            messages.append(
                f":heavy_check_mark: The commit {commit.sha} is not a squash commit",
            )
        if (
            commit_message_config.get(
                "check-first-capital",
                checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIRST_CAPITAL_DEFAULT,
            )
            and first_capital.match(head) is None
        ):
            _LOGGER.warning("The first letter of message head should be a capital")
            messages.append(
                f":x: The first letter of message head should be a capital in commit {commit.sha}",
            )
            success = False
        else:
            messages.append(
                f":heavy_check_mark: The first letter of message head in commit {commit.sha} is a capital",
            )
        min_length = commit_message_config.get(
            "min-head-length",
            checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_MIN_HEAD_LENGTH_DEFAULT,
        )
        if min_length > 0 and len(head) < min_length:
            _LOGGER.warning(
                "The message head should be at least %i characters long",
                min_length,
            )
            messages.append(
                f":x: The message head should be at least {min_length} characters long in commit {commit.sha}",
            )
            success = False
        else:
            messages.append(
                f":heavy_check_mark: The message head in commit {commit.sha} is at least {min_length} characters long",
            )
        if (
            commit_message_config.get(
                "check-no-merge-commits",
                checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_NO_MERGE_COMMITS_DEFAULT,
            )
            and len(commit.parents) != 1
        ):
            _LOGGER.warning("The merge commit are not allowed")
            messages.append(
                f":x: The merge commit are not allowed in commit {commit.sha}",
            )
            success = False
        else:
            messages.append(
                f":heavy_check_mark: The commit {commit.sha} is not a merge commit",
            )
        if commit_message_config.get(
            "check-no-own-revert",
            checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_NO_OWN_REVERT_DEFAULT,
        ) and (
            head.startswith("Revert ")
            and len(message_lines) == 3
            and message_lines[2].startswith("This reverts commit ")
        ):
            revert_commit_hash = message_lines[2][len("This reverts commit ") : -1]
            if revert_commit_hash in commit_hash:
                _LOGGER.warning(
                    "Revert own commits is not allowed (%s)",
                    revert_commit_hash,
                )
                messages.append(
                    f":heavy_check_mark: Revert own commits is not allowed in commit {commit.sha}",
                )
                success = False
                continue
            messages.append(
                f":heavy_check_mark: The commit {commit.sha} is not an own revert commit",
            )
    return success, messages


async def _commits_spell(
    config: checks_configuration.PullRequestChecksConfiguration,
    commits: list[githubkit.versions.latest.models.Commit],
    spellcheck_cmd: list[str],
) -> tuple[bool, list[str]]:
    """Check the spelling of the commits body."""
    messages = []
    success = True
    for commit in commits:
        with tempfile.NamedTemporaryFile(
            "w+t",
            encoding="utf-8",
            suffix=".yaml",
        ) as temp_file:
            if config.get(
                "only-head",
                checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_ONLY_HEAD_DEFAULT,
            ):
                head = commit.commit.message.split("\n")[0]
                temp_file.write(head)
            else:
                temp_file.write(commit.commit.message)
            temp_file.flush()
            command = [*spellcheck_cmd, temp_file.name]
            spell = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
            async with asyncio.timeout(120):
                stdout, stderr = await spell.communicate()
            message = module_utils.AnsiProcessMessage.from_async_artifacts(
                command,
                spell,
                stdout,
                stderr,
            )
            if spell.returncode != 0:
                message.title = f"Code spell error in commit {commit.sha}"
                _LOGGER.warning(message)
                success = False
                messages.append(
                    ":x: " + message.title + "\n" + module_utils.html_to_markdown(message.stdout),
                )
            else:
                messages.append(
                    f":heavy_check_mark: Code spell on commit {commit.sha} are correct",
                )
            message.title = f"Code spell in commit {commit.sha}"
            _LOGGER.debug(message)
    return success, messages


async def _pull_request_spell(
    config: checks_configuration.PullRequestChecksConfiguration,
    pull_request: githubkit.versions.latest.models.PullRequest,
    spellcheck_cmd: list[str],
) -> tuple[bool, list[str]]:
    """Check the spelling of the pull request title and message."""
    messages = []
    with tempfile.NamedTemporaryFile("w+t") as temp_file:
        temp_file.write(pull_request.title)
        temp_file.write("\n")
        if (
            not config.get(
                "only_head",
                checks_configuration.PULL_REQUEST_CHECKS_ONLY_HEAD_DEFAULT,
            )
            and pull_request.body
        ):
            temp_file.write("\n")
            temp_file.write(pull_request.body)
            temp_file.write("\n")
        temp_file.flush()
        command = [*spellcheck_cmd, temp_file.name]
        spell = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(60):
            stdout, stderr = await spell.communicate()
        message = module_utils.AnsiProcessMessage.from_async_artifacts(
            command,
            spell,
            stdout,
            stderr,
        )
        if spell.returncode != 0:
            message.title = "Code spell error in pull request"
            _LOGGER.warning(message)
            messages.append(
                ":x: " + message.title + "\n" + module_utils.html_to_markdown(message.stdout),
            )
            return False, messages
        messages.append(
            (
                ":heavy_check_mark: Pull request title is correct"
                if config.get(
                    "only_head",
                    checks_configuration.PULL_REQUEST_CHECKS_ONLY_HEAD_DEFAULT,
                )
                else ":heavy_check_mark: Pull request title and body are correct"
            ),
        )

        message.title = "Code spell in pull request"
        _LOGGER.debug(message)
    return True, messages


class Checks(
    module.Module[
        checks_configuration.PullRequestChecksConfiguration,
        dict[str, Any],
        dict[str, Any],
        None,
    ],
):
    """Module to check the pull request message and commits."""

    def title(self) -> str:
        """Get the title."""
        return "Pull request checks"

    def description(self) -> str:
        """Get the description."""
        return "Check the pull request spelling, and commits"

    def documentation_url(self) -> str:
        """Get the documentation URL."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/pull_request/Checks.md"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {},
            {"pull_request"},
        )

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with (Path(__file__).parent / "checks-schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[dict[str, Any]]]:
        """Get the actions to execute."""
        if context.module_event_name == "pull_request":
            event_data = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            if event_data.action in ("opened", "reopened", "edited", "synchronize"):
                return [
                    module.Action(
                        {
                            "pull-request-number": event_data.pull_request.number,
                        },
                        checks=True,
                        priority=module.PRIORITY_STATUS,
                    ),
                ]
        return []

    async def process(
        self,
        context: module.ProcessContext[
            checks_configuration.PullRequestChecksConfiguration,
            dict[str, Any],
        ],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """Process the module."""

        assert context.module_event_name == "pull_request"
        event_data = githubkit.webhooks.parse_obj(
            "pull_request",
            context.github_event_data,
        )

        # Get the pull request and commits concurrently
        pull_request_response, commits_response = await asyncio.gather(
            context.github_project.aio_github.rest.pulls.async_get(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                pull_number=context.module_event_data["pull-request-number"],
            ),
            context.github_project.aio_github.rest.pulls.async_list_commits(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                pull_number=context.module_event_data["pull-request-number"],
            ),
        )
        pull_request = pull_request_response.parsed_data
        commits = commits_response.parsed_data

        with tempfile.NamedTemporaryFile("w+t", encoding="utf-8") as ignore_file:
            spellcheck_cmd = await _get_code_spell_command(
                context,
                event_data,
                ignore_file,
            )
            success_1, messages_1 = _commits_messages(context.module_config, commits)
            success_2, messages_2 = await _commits_spell(
                context.module_config,
                commits,
                spellcheck_cmd,
            )
            success_3, messages_3 = await _pull_request_spell(
                context.module_config,
                pull_request,
                spellcheck_cmd,
            )
        success = success_1 and success_2 and success_3
        message = "\n".join([*messages_1, *messages_2, *messages_3])

        return module.ProcessOutput(success=success, output={"summary": message})
