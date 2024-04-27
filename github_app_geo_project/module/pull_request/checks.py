"""Module to display the status of the workflows in the transversal dashboard."""

import json
import logging
import os
import re
import subprocess  # nosec
import urllib
from tempfile import NamedTemporaryFile
from typing import Any

import github
import github.Commit
import github.PullRequest

from github_app_geo_project import configuration, module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.pull_request import checks_configuration

_LOGGER = logging.getLogger(__name__)


def _get_codespell_command(config: checks_configuration.PullRequestChecksConfiguration) -> list[str]:
    """
    Get the codespell command.
    """
    codespell_config = config.get("codespell", {})
    codespell_config = codespell_config if isinstance(codespell_config, dict) else {}
    command = ["codespell"]
    for spell_ignore_file in (
        ".github/spell-ignore-words.txt",
        "spell-ignore-words.txt",
        ".spell-ignore-words.txt",
    ):
        if os.path.exists(spell_ignore_file):
            command.append(f"--ignore-words={spell_ignore_file}")
            break
    dictionaries = codespell_config.get(
        "internal-dictionaries", checks_configuration.CODESPELL_DICTIONARIES_DEFAULT
    )
    if dictionaries:
        command.append("--builtin=" + ",".join(dictionaries))
    command += codespell_config.get("arguments", checks_configuration.CODESPELL_ARGUMENTS_DEFAULT)
    return command


def _commits_messages(
    config: checks_configuration.PullRequestChecksConfiguration,
    commits: list[github.Commit.Commit],
) -> bool:
    """
    Check the commits messages.

    - They should start with a capital letter.
    - They should not be too short.
    - They should not be a squash or fixup commit.
    - They should not be a merge commit.
    - They should not be a revert commit.
    """
    commit_message_config = config.get("commits-messages", {})
    if commit_message_config is False:
        return True
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
            "check-fixup", checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIXUP_DEFAULT
        ) and head.startswith("fixup! "):
            _LOGGER.warning("Fixup message not allowed")
            success = False
        if commit_message_config.get(
            "check-squash", checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_SQUASH_DEFAULT
        ) and head.startswith("squash! "):
            _LOGGER.warning("Squash message not allowed")
            success = False
        if (
            commit_message_config.get(
                "check-first-capital",
                checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIRST_CAPITAL_DEFAULT,
            )
            and first_capital.match(head) is None
        ):
            _LOGGER.warning("The first letter of message head should be a capital")
            success = False
        min_length = commit_message_config.get(
            "min-head-length",
            checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_MIN_HEAD_LENGTH_DEFAULT,
        )
        if min_length > 0 and len(head) < min_length:
            _LOGGER.warning("The message head should be at least %i characters long", min_length)
            success = False
        if (
            commit_message_config.get(
                "check-no-merge-commits",
                checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_NO_MERGE_COMMITS_DEFAULT,
            )
            and len(commit.parents) != 1
        ):
            _LOGGER.warning("The merge commit are not allowed")
            success = False
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
                _LOGGER.warning("Revert own commits is not allowed (%s)", revert_commit_hash)
                success = False
                continue
    return success


def _commits_spell(
    config: checks_configuration.PullRequestChecksConfiguration,
    commits: list[github.Commit.Commit],
) -> bool:
    """Check the spelling of the commits body."""
    spellcheck_cmd = _get_codespell_command(config)

    success = True
    for commit in commits:
        with NamedTemporaryFile("w+t", encoding="utf-8", suffix=".yaml") as temp_file:
            if config.get(
                "only-head", checks_configuration.PULL_REQUEST_CHECKS_COMMITS_MESSAGES_ONLY_HEAD_DEFAULT
            ):
                head = commit.commit.message.split("\n")[0]
                temp_file.write(head)
            else:
                temp_file.write(commit.commit.message)
            temp_file.flush()
            spell = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                spellcheck_cmd + [temp_file.name], capture_output=True, encoding="utf-8"
            )
            message = module_utils.ansi_proc_message(spell)
            if spell.returncode != 0:
                message.title = f"Code spell error in commit {commit.sha}"
                _LOGGER.warning(message)
                success = False
            message.title = f"Code spell in commit {commit.sha}"
            _LOGGER.debug(message)
    return success


def _pull_request_spell(
    config: checks_configuration.PullRequestChecksConfiguration, pull_request: github.PullRequest.PullRequest
) -> bool:
    """Check the spelling of the pull request title and message."""
    spellcheck_cmd = _get_codespell_command(config)

    with NamedTemporaryFile("w+t") as temp_file:
        temp_file.write(pull_request.title)
        temp_file.write("\n")
        if (
            not config.get("only_head", checks_configuration.PULL_REQUEST_CHECKS_ONLY_HEAD_DEFAULT)
            and pull_request.body
        ):
            temp_file.write("\n")
            temp_file.write(pull_request.body)
            temp_file.write("\n")
        temp_file.flush()
        spell = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
            spellcheck_cmd + [temp_file.name], capture_output=True, encoding="utf-8"
        )
        message = module_utils.ansi_proc_message(spell)
        if spell.returncode != 0:
            message.title = "Code spell error in pull request"
            _LOGGER.warning(message)
            return False
        message.title = "Code spell in pull request"
        _LOGGER.debug(message)
    return True


class Checks(module.Module[checks_configuration.PullRequestChecksConfiguration]):
    """Module to check the pull request message and commits."""

    def title(self) -> str:
        """Get the title."""
        return "Pull request checks"

    def description(self) -> str:
        """Get the description."""
        return "Check the pull request spelling, and commits"

    def documentation_url(self) -> str:
        """Get the documentation URL."""
        return (
            "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Pull-request-checks"
        )

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "checks": "write",
            },
            {"pull_request"},
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with open(
            os.path.join(os.path.dirname(__file__), "checks-schema.json"), encoding="utf-8"
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """Get the actions to execute."""
        if (
            context.event_data.get("action") in ("opened", "reopened", "synchronize")
            and "pull_request" in context.event_data
        ):
            full_name = context.event_data.get("repository", {}).get("full_name")
            owner, repo = full_name.split("/")
            github_project = configuration.get_github_project({}, context.github_application, owner, repo)
            repo = github_project.github.get_repo(full_name)
            check_run = repo.create_check_run(
                "Pull request checks",
                context.event_data.get("pull_request", {}).get("head", {}).get("sha"),
            )
            return [
                module.Action(
                    {
                        "pull-request-number": context.event_data.get("pull_request", {}).get("number"),
                        "check-run-id": check_run.id,
                    }
                )
            ]
        return []

    def process(
        self, context: module.ProcessContext[checks_configuration.PullRequestChecksConfiguration]
    ) -> module.ProcessOutput | None:
        """Process the module."""
        repo = context.github_project.github.get_repo(
            context.github_project.owner + "/" + context.github_project.repository
        )

        service_url = context.service_url
        service_url = service_url if service_url.endswith("/") else service_url + "/"
        service_url = urllib.parse.urljoin(service_url, "logs/")
        service_url = urllib.parse.urljoin(service_url, str(context.job_id))

        check_run = repo.get_check_run(context.module_data["check-run-id"])
        check_run.edit(status="in_progress", details_url=service_url)

        pull_request = repo.get_pull(number=context.module_data["pull-request-number"])
        commits = [  # pylint: disable=unnecessary-comprehension
            commit for commit in pull_request.get_commits()
        ]

        success = _commits_messages(context.module_config, commits)
        success &= _commits_spell(context.module_config, commits)
        success &= _pull_request_spell(context.module_config, pull_request)

        check_run.edit(
            status="completed",
            conclusion="success" if success else "failure",
        )

        return module.ProcessOutput(transversal_status=context.module_data)

    def cleanup(self, context: module.CleanupContext) -> None:
        """Cleanup the module."""
        repo = context.github_project.github.get_repo(
            context.github_project.owner + "/" + context.github_project.repository
        )
        check_run = repo.get_check_run(context.module_data["check-run-id"])
        check_run.edit(
            status="completed",
            conclusion="skipped",
        )
