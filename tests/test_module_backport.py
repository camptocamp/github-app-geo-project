# Copyright (c) 2026, Camptocamp SA

"""Tests for the backport module."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import githubkit.exception
import githubkit_schemas.latest.models
import pytest

from github_app_geo_project import configuration, module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.backport import (
    Backport,
    _clean_backport_body,
    _existing_branch_blocker,
    _labels_to_copy,
)
from github_app_geo_project.module.utils import PullRequestResult, PullRequestStatus

_BOT_LOGIN = "my-app[bot]"
_BOT_EMAIL = f"12345+{_BOT_LOGIN}@users.noreply.github.com"
_BACKPORT_BRANCH = "ghci/backport/12-to-2.9"


def _make_request_failed_exception(status_code: int) -> githubkit.exception.RequestFailed:
    response = MagicMock()
    response.status_code = status_code
    return githubkit.exception.RequestFailed(response)


def _make_context() -> MagicMock:
    context = MagicMock(spec=module.ProcessContext)
    context.github_project = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.github_project.application.slug = "my-app"
    return context


def _make_head_commit(login: str | None, email: str) -> MagicMock:
    head_commit = MagicMock()
    if login is None:
        head_commit.committer = None
    else:
        head_commit.committer = MagicMock(spec=githubkit_schemas.latest.models.SimpleUser)
        head_commit.committer.login = login
    head_commit.commit.committer.email = email
    return head_commit


def _make_label(name: str) -> MagicMock:
    label = MagicMock()
    label.name = name
    return label


def _make_event_data(labels: list[str]) -> MagicMock:
    event_data = MagicMock()
    event_data.pull_request.number = 12
    event_data.pull_request.title = "My change"
    event_data.pull_request.labels = [_make_label(label) for label in labels]
    return event_data


@asynccontextmanager
async def _fake_working_tree(
    github_project: configuration.GithubProject,
    branch: str,
) -> AsyncIterator[anyio.Path]:
    yield anyio.Path("/tmp/ghci-backport-test")


def test_labels_to_copy_excludes_the_backport_labels() -> None:
    """The backport labels are not copied to not request a backport of the backport pull request."""
    assert _labels_to_copy(["bug", "backport 2.9", "enhancement", "backport 2.10"]) == [
        "bug",
        "enhancement",
    ]


def test_labels_to_copy_deduplicates_and_sorts() -> None:
    """The copied labels are deduplicated and sorted to get a stable result."""
    assert _labels_to_copy(["dependencies", "bug", "dependencies"]) == ["bug", "dependencies"]


def test_labels_to_copy_without_label() -> None:
    """A pull request without any copyable label produces an empty list."""
    assert _labels_to_copy([]) == []
    assert _labels_to_copy(["backport 2.9"]) == []


@pytest.mark.asyncio
async def test_existing_branch_blocker_missing_branch() -> None:
    """A branch that does not exist can be created."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        side_effect=_make_request_failed_exception(404),
    )
    assert await _existing_branch_blocker(context, _BACKPORT_BRANCH) is None


@pytest.mark.asyncio
async def test_existing_branch_blocker_bot_committer() -> None:
    """A branch whose head commit was committed by the application can be overwritten."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        return_value=MagicMock(parsed_data=_make_head_commit(_BOT_LOGIN, _BOT_EMAIL)),
    )
    assert await _existing_branch_blocker(context, _BACKPORT_BRANCH) is None


@pytest.mark.asyncio
async def test_existing_branch_blocker_bot_email() -> None:
    """The committer email is used when GitHub does not resolve the committer to an account."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        return_value=MagicMock(parsed_data=_make_head_commit(None, _BOT_EMAIL)),
    )
    assert await _existing_branch_blocker(context, _BACKPORT_BRANCH) is None


@pytest.mark.asyncio
async def test_existing_branch_blocker_human_committer() -> None:
    """A branch whose head commit comes from somebody else is never overwritten."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        return_value=MagicMock(parsed_data=_make_head_commit("contributor", "contributor@example.com")),
    )
    blocker = await _existing_branch_blocker(context, _BACKPORT_BRANCH)
    assert blocker is not None
    assert _BACKPORT_BRANCH in blocker


@pytest.mark.asyncio
async def test_existing_branch_blocker_api_error() -> None:
    """An API error other than a missing branch is raised."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        side_effect=_make_request_failed_exception(500),
    )
    with pytest.raises(githubkit.exception.RequestFailed):
        await _existing_branch_blocker(context, _BACKPORT_BRANCH)


async def _run_backport(
    context: MagicMock,
    labels: list[str],
    pull_request_result: PullRequestResult,
    add_labels_side_effect: Exception | None = None,
) -> tuple[str | None, MagicMock]:
    """Run the backport of a one commit pull request with every Git and GitHub call mocked."""
    commit = MagicMock()
    commit.sha = "abc123"
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        side_effect=_make_request_failed_exception(404),
    )
    context.github_project.aio_github.rest.pulls.async_list_commits = AsyncMock(
        return_value=MagicMock(parsed_data=[commit]),
    )
    context.github_project.aio_github.rest.issues.async_add_labels = AsyncMock(
        side_effect=add_labels_side_effect,
    )
    context.github_project.aio_github.rest.issues.async_remove_label = AsyncMock()
    with (
        patch.object(module_utils.GIT_WORKTREE_CACHE, "working_tree", new=_fake_working_tree),
        patch.object(module_utils, "run_timeout", new=AsyncMock(return_value=("", True, None))),
        patch.object(module_utils, "create_pull_request", new=AsyncMock(return_value=pull_request_result)),
    ):
        error = await Backport()._backport(context, _make_event_data(labels), "2.9")
    return error, context.github_project.aio_github.rest.issues


@pytest.mark.asyncio
async def test_backport_copies_the_labels() -> None:
    """The labels of the source pull request are copied to the created backport pull request."""
    context = _make_context()
    backport_pull_request = MagicMock()
    backport_pull_request.number = 42
    error, issues = await _run_backport(
        context,
        ["bug", "backport 2.9", "dependencies"],
        PullRequestResult(PullRequestStatus.CREATED, backport_pull_request),
    )
    assert error is None
    issues.async_add_labels.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=42,
        data=["bug", "dependencies"],
    )
    issues.async_remove_label.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=12,
        name="backport 2.9",
    )


@pytest.mark.asyncio
async def test_backport_updates_the_labels_of_an_existing_pull_request() -> None:
    """An already opened backport pull request gets the labels too."""
    context = _make_context()
    backport_pull_request = MagicMock()
    backport_pull_request.number = 42
    error, issues = await _run_backport(
        context,
        ["bug"],
        PullRequestResult(PullRequestStatus.UPDATED, backport_pull_request),
    )
    assert error is None
    issues.async_add_labels.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=42,
        data=["bug"],
    )


@pytest.mark.asyncio
async def test_backport_without_label() -> None:
    """No label call is done when there is nothing to copy."""
    context = _make_context()
    backport_pull_request = MagicMock()
    backport_pull_request.number = 42
    error, issues = await _run_backport(
        context,
        ["backport 2.9"],
        PullRequestResult(PullRequestStatus.CREATED, backport_pull_request),
    )
    assert error is None
    issues.async_add_labels.assert_not_awaited()


@pytest.mark.asyncio
async def test_backport_label_error_does_not_fail() -> None:
    """A failing label copy is logged but does not hide the created backport pull request."""
    context = _make_context()
    backport_pull_request = MagicMock()
    backport_pull_request.number = 42
    error, issues = await _run_backport(
        context,
        ["bug"],
        PullRequestResult(PullRequestStatus.CREATED, backport_pull_request),
        add_labels_side_effect=_make_request_failed_exception(422),
    )
    assert error is None
    issues.async_add_labels.assert_awaited_once()
    issues.async_remove_label.assert_awaited_once()


@pytest.mark.asyncio
async def test_backport_pull_request_error() -> None:
    """A failed pull request creation is reported instead of being hidden as a success."""
    context = _make_context()
    error, issues = await _run_backport(
        context,
        ["bug"],
        PullRequestResult(PullRequestStatus.ERROR),
    )
    assert error == "Error while creating the backport pull request"
    issues.async_add_labels.assert_not_awaited()


@pytest.mark.asyncio
async def test_backport_pull_request_stale() -> None:
    """The reason of a stale pull request is reported."""
    context = _make_context()
    error, _ = await _run_backport(
        context,
        ["bug"],
        PullRequestResult(PullRequestStatus.STALE, MagicMock(), "Open for 7 days"),
    )
    assert error == "Open for 7 days"


@pytest.mark.asyncio
async def test_backport_existing_branch_not_created_by_the_application() -> None:
    """An existing branch whose head commit comes from somebody else blocks the backport."""
    context = _make_context()
    context.github_project.aio_github.rest.repos.async_get_commit = AsyncMock(
        return_value=MagicMock(parsed_data=_make_head_commit("contributor", "contributor@example.com")),
    )
    context.github_project.aio_github.rest.pulls.async_list_commits = AsyncMock()
    with patch.object(module_utils.GIT_WORKTREE_CACHE, "working_tree", new=_fake_working_tree):
        error = await Backport()._backport(context, _make_event_data(["bug"]), "2.9")
    assert error is not None
    assert _BACKPORT_BRANCH in error
    context.github_project.aio_github.rest.pulls.async_list_commits.assert_not_awaited()


_CLEAN_BODY = "Backport of #12 to 2.9"
_RESOLVED_BODY = f"{_CLEAN_BODY}\n\nConflicts resolved manually"
_FAILED_BODY = f"{_CLEAN_BODY}\n\nError on cherry-picking: abc123\n\nTo continue do:\n```bash\ngit fetch\n```"


def test_clean_backport_body_current_prefix() -> None:
    """The clean body is rebuilt from the current backport branch name."""
    assert _clean_backport_body("ghci/backport/12-to-2.9") == _CLEAN_BODY


def test_clean_backport_body_legacy_prefix() -> None:
    """The legacy backport branch prefix is also recognized."""
    assert _clean_backport_body("backport/12-to-2.9") == _CLEAN_BODY


def test_clean_backport_body_branch_with_hyphens() -> None:
    """A target branch containing `-to-` is fully captured by the greedy pattern."""
    assert _clean_backport_body("ghci/backport/7-to-int-2-9") == "Backport of #7 to int-2-9"


def test_clean_backport_body_not_a_backport_branch() -> None:
    """A branch that does not match the pattern returns None to never rewrite an unexpected body."""
    assert _clean_backport_body("feature/my-branch") is None
    assert _clean_backport_body("ghci/backport/not-a-number") is None


async def _reset_body(current_body: str | None, head_ref: str = _BACKPORT_BRANCH) -> MagicMock:
    """Run `_reset_resolved_body` with the GitHub update call mocked, returning the pulls mock."""
    context = _make_context()
    pulls: MagicMock = context.github_project.aio_github.rest.pulls
    pulls.async_update = AsyncMock()
    await Backport()._reset_resolved_body(context, head_ref, current_body, 42)
    return pulls


@pytest.mark.asyncio
async def test_reset_resolved_body_rewrites_the_instructions() -> None:
    """A generated body still holding the failure instructions is replaced by the resolved note."""
    pulls = await _reset_body(_FAILED_BODY)
    pulls.async_update.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        pull_number=42,
        body=_RESOLVED_BODY,
    )


@pytest.mark.asyncio
async def test_reset_resolved_body_clean_backport_untouched() -> None:
    """A backport that applied cleanly has no instruction to remove, its body is left as is."""
    pulls = await _reset_body(_CLEAN_BODY)
    pulls.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_resolved_body_idempotent() -> None:
    """An already resolved body is not rewritten a second time."""
    pulls = await _reset_body(_RESOLVED_BODY)
    pulls.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_resolved_body_customized_untouched() -> None:
    """A body that does not start with the generated message was customized and is never touched."""
    pulls = await _reset_body("My own backport description\n\nWith details")
    pulls.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_resolved_body_none_untouched() -> None:
    """A pull request without a body is left as is."""
    pulls = await _reset_body(None)
    pulls.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_resolved_body_unexpected_branch_untouched() -> None:
    """A pull request whose head branch does not match the pattern is never rewritten."""
    pulls = await _reset_body(_FAILED_BODY, head_ref="feature/my-branch")
    pulls.async_update.assert_not_awaited()
