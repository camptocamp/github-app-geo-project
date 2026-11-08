# Copyright (c) 2026, Camptocamp SA

import base64
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import githubkit.exception
import githubkit_schemas.latest.models
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.pull_request import checks, checks_configuration

_SECURITY_MD_CONTENT = "| Version | Supported Until |\n|---|---|\n| 2.9 | 01/01/2026 |\n| 2.8 | Unsupported |"


def get_commit_mock(
    sha: str, message: str, parents_number: int = 1
) -> githubkit_schemas.latest.models.Commit:
    """Create a mock Commit for testing."""
    commit = MagicMock(spec=githubkit_schemas.latest.models.Commit)
    commit.sha = sha
    commit.commit = MagicMock(spec=githubkit_schemas.latest.models.GitCommit)
    commit.commit.message = message
    commit.parents = [MagicMock() for _ in range(parents_number)]
    return commit


def create_mock_context(security_md_content: str | None = _SECURITY_MD_CONTENT) -> MagicMock:
    """Create a mock ProcessContext for testing."""
    context = MagicMock(spec=module.ProcessContext)
    context.module_config = checks_configuration.PullRequestChecksConfiguration()
    context.module_event_name = "pull_request"
    context.module_event_data = {"pull-request-number": 1}

    github_project = MagicMock()
    context.github_project = github_project
    github_project.owner = "owner"
    github_project.repository = "repository"
    github_project.default_branch = AsyncMock(return_value="master")

    aio_github = MagicMock()
    github_project.aio_github = aio_github

    get_content_response = MagicMock()
    if security_md_content is None:
        not_found_response = MagicMock()
        not_found_response.status_code = 404
        aio_github.rest.repos.async_get_content = AsyncMock(
            side_effect=githubkit.exception.RequestFailed(not_found_response),
        )
    else:
        get_content_response.parsed_data = MagicMock(spec=githubkit_schemas.latest.models.ContentFile)
        get_content_response.parsed_data.content = base64.b64encode(
            security_md_content.encode("utf-8")
        ).decode(
            "utf-8",
        )
        aio_github.rest.repos.async_get_content = AsyncMock(return_value=get_content_response)

    return context


@pytest.mark.asyncio
async def test_get_stabilization_branches() -> None:
    context = create_mock_context()
    branches = await checks._get_stabilization_branches(context)
    assert branches == ["master", "2.9"]


@pytest.mark.asyncio
async def test_get_stabilization_branches_no_security_md() -> None:
    context = create_mock_context(security_md_content=None)
    branches = await checks._get_stabilization_branches(context)
    assert branches == ["master"]


def test_commits_messages_merge_commit_not_allowed() -> None:
    commits = [get_commit_mock("sha1", "Merge branch 'master' into 2.9", parents_number=2)]
    success, messages = checks._commits_messages({}, commits)
    assert not success
    assert ":x: The merge commit are not allowed in commit sha1" in messages


def test_commits_messages_merge_commit_allowed() -> None:
    commits = [get_commit_mock("sha1", "Merge branch 'master' into 2.9", parents_number=2)]
    success, messages = checks._commits_messages({}, commits, allow_merge_commits=True)
    assert success
    assert any("The merge commit sha1 is allowed" in message for message in messages)


def test_commits_messages_not_merge_commit() -> None:
    commits = [get_commit_mock("sha1", "A normal commit message")]
    success, messages = checks._commits_messages({}, commits, allow_merge_commits=True)
    assert success
    assert ":heavy_check_mark: The commit sha1 is not a merge commit" in messages


def test_commits_messages_other_checks_still_apply() -> None:
    commits = [get_commit_mock("sha1", "fixup! A normal commit message", parents_number=2)]
    success, messages = checks._commits_messages({}, commits, allow_merge_commits=True)
    assert not success
    assert ":x: Fixup message not allowed in commit sha1" in messages


def get_pull_request_mock(head_ref: str, head_repo_full_name: str = "owner/repository") -> MagicMock:
    """Create a mock PullRequest for testing."""
    pull_request = MagicMock(spec=githubkit_schemas.latest.models.PullRequest)
    pull_request.number = 1
    pull_request.title = "A pull request title"
    pull_request.body = "A pull request body."
    pull_request.head = MagicMock(spec=githubkit_schemas.latest.models.PullRequestPropHead)
    pull_request.head.ref = head_ref
    pull_request.head.sha = "headsha"
    pull_request.head.repo = MagicMock(spec=githubkit_schemas.latest.models.Repository)
    pull_request.head.repo.full_name = head_repo_full_name
    return pull_request


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("head_ref", "head_repo_full_name", "expected_allowed"),
    [
        ("2.9", "owner/repository", True),
        ("master", "owner/repository", True),
        ("2.8", "owner/repository", False),
        ("feature-branch", "owner/repository", False),
        ("2.9", "fork/repository", False),
    ],
)
async def test_process_allow_merge_commits(
    head_ref: str,
    head_repo_full_name: str,
    expected_allowed: bool,
) -> None:
    context = create_mock_context()
    pull_request = get_pull_request_mock(head_ref, head_repo_full_name)
    commits = [get_commit_mock("sha1", "Merge branch 'master' into 2.9", parents_number=2)]

    pull_request_response = MagicMock()
    pull_request_response.parsed_data = pull_request
    commits_response = MagicMock()
    commits_response.parsed_data = commits
    context.github_project.aio_github.rest.pulls.async_get = AsyncMock(return_value=pull_request_response)
    context.github_project.aio_github.rest.pulls.async_list_commits = AsyncMock(return_value=commits_response)

    event_data = MagicMock()
    checks_module = checks.Checks()

    with (
        mock.patch("githubkit.webhooks.parse_obj", return_value=event_data),
        mock.patch(
            "github_app_geo_project.module.pull_request.checks._get_code_spell_command",
            new_callable=AsyncMock,
            return_value=["codespell"],
        ),
        mock.patch(
            "github_app_geo_project.module.pull_request.checks._commits_spell",
            new_callable=AsyncMock,
            return_value=(True, []),
        ),
        mock.patch(
            "github_app_geo_project.module.pull_request.checks._pull_request_spell",
            new_callable=AsyncMock,
            return_value=(True, []),
        ),
        mock.patch(
            "github_app_geo_project.module.pull_request.checks._commits_messages",
            wraps=checks._commits_messages,
        ) as mock_commits_messages,
    ):
        output = await checks_module.process(context)

    assert mock_commits_messages.call_args.args[2] is expected_allowed
    assert output.success is expected_allowed
