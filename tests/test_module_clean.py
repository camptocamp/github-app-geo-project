# Copyright (c) 2026, Camptocamp SA

"""Tests for the clean module."""

import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from github_app_geo_project.module.clean import Clean, configuration
from github_app_geo_project.module.utils import Message


def _make_worktree_mock(clone_path: Path) -> MagicMock:
    """Create a mock for GIT_WORKTREE_CACHE.working_tree async context manager."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(clone_path))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


def _make_clean_context(names: list[str], event_type: str = "pull_request") -> MagicMock:
    context = MagicMock()
    context.module_event_data = MagicMock()
    context.module_event_data.type = event_type
    context.module_event_data.names = names
    return context


def _make_fake_run_timeout(commands: list[list[str]]):
    async def fake_run_timeout(
        command: list[str],
        env: dict[str, str] | None,
        timeout: datetime.timedelta | int,
        success_message: str,
        error_message: str,
        timeout_message: str,
        cwd: anyio.Path,
        error: bool = True,
    ) -> tuple[str | None, bool, Message | None]:
        commands.append(command)
        return "", True, None

    return fake_run_timeout


async def _aiter(values):
    for value in values:
        yield value


def _make_pull_request_event(*, merged: bool, head_ref: str = "ghci/test-bot"):
    event_data = MagicMock()
    event_data.action = "closed"
    event_data.pull_request = MagicMock()
    event_data.pull_request.merged = merged
    event_data.pull_request.number = 42
    event_data.pull_request.head = MagicMock()
    event_data.pull_request.head.ref = head_ref
    event_data.pull_request.head.repo = MagicMock(id=1)
    event_data.pull_request.base = MagicMock()
    event_data.pull_request.base.repo = MagicMock(id=1)
    return event_data


def _make_bot_commit():
    commit = MagicMock()
    commit.author = MagicMock(login="renovate[bot]")
    commit.committer = MagicMock(login="renovate[bot]")
    commit.commit = MagicMock()
    commit.commit.author = MagicMock(
        name="renovate[bot]", email="29139614+renovate[bot]@users.noreply.github.com"
    )
    commit.commit.committer = MagicMock(
        name="renovate[bot]", email="29139614+renovate[bot]@users.noreply.github.com"
    )
    return commit


def _make_human_commit():
    commit = MagicMock()
    commit.author = MagicMock(login="alice")
    commit.committer = MagicMock(login="alice")
    commit.commit = MagicMock()
    commit.commit.author = MagicMock(name="Alice", email="alice@example.com")
    commit.commit.committer = MagicMock(name="Alice", email="alice@example.com")
    return commit


def _make_context(commits):
    context = MagicMock()
    context.module_event_data.type = "pull_request"
    context.module_config = {"docker": False, "git": []}
    context.github_event_data = {"repository": {"default_branch": "main"}}
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.github_project.aio_github.paginate = MagicMock(return_value=_aiter(commits))
    context.github_project.aio_github.rest.git.async_delete_ref = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_process_delete_branch_on_closed_non_merged_bot_only_pull_request() -> None:
    clean_module = Clean()
    context = _make_context([_make_bot_commit()])

    with patch(
        "githubkit.webhooks.parse_obj",
        return_value=_make_pull_request_event(merged=False),
    ):
        await clean_module.process(context)

    context.github_project.aio_github.rest.git.async_delete_ref.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        ref="heads/ghci/test-bot",
    )


@pytest.mark.asyncio
async def test_process_do_not_delete_branch_when_pull_request_has_human_commit() -> None:
    clean_module = Clean()
    context = _make_context([_make_bot_commit(), _make_human_commit()])

    with patch(
        "githubkit.webhooks.parse_obj",
        return_value=_make_pull_request_event(merged=False),
    ):
        await clean_module.process(context)

    context.github_project.aio_github.rest.git.async_delete_ref.assert_not_called()


@pytest.mark.asyncio
async def test_process_do_not_delete_branch_when_pull_request_is_merged() -> None:
    clean_module = Clean()
    context = _make_context([_make_bot_commit()])

    with patch(
        "githubkit.webhooks.parse_obj",
        return_value=_make_pull_request_event(merged=True),
    ):
        await clean_module.process(context)

    context.github_project.aio_github.rest.git.async_delete_ref.assert_not_called()


@pytest.mark.asyncio
async def test_clean_git_skip_worktree_and_push_when_no_folder_exists() -> None:
    clean_module = Clean()
    context = _make_clean_context(["42", "feature-branch"])
    git_config: configuration.Git = {"branch": "gh-pages", "folder": "refs/pull/{name}", "amend": True}

    with (
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.any_path_exists",
            new=AsyncMock(return_value=False),
        ) as mock_any_path_exists,
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.working_tree",
        ) as mock_working_tree,
    ):
        await clean_module._clean_git(context, git_config)

    mock_any_path_exists.assert_awaited_once_with(
        context.github_project,
        "gh-pages",
        ["refs/pull/42", "refs/pull/feature-branch"],
    )
    mock_working_tree.assert_not_called()


@pytest.mark.asyncio
async def test_clean_git_no_push_when_nothing_cleaned(tmp_path: Path) -> None:
    clean_module = Clean()
    context = _make_clean_context(["42"])
    git_config: configuration.Git = {"branch": "gh-pages", "folder": "refs/pull/{name}", "amend": True}

    clone_path = tmp_path / "repo"
    clone_path.mkdir()

    commands: list[list[str]] = []

    with (
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.any_path_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.working_tree",
            return_value=_make_worktree_mock(clone_path),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.run_timeout",
            new=_make_fake_run_timeout(commands),
        ),
    ):
        await clean_module._clean_git(context, git_config)

    assert commands == []


@pytest.mark.asyncio
async def test_clean_git_amend_and_push_when_folder_removed(tmp_path: Path) -> None:
    clean_module = Clean()
    context = _make_clean_context(["42"])
    git_config: configuration.Git = {"branch": "gh-pages", "folder": "refs/pull/{name}", "amend": True}

    clone_path = tmp_path / "repo"
    folder_path = clone_path / "refs" / "pull" / "42"
    folder_path.mkdir(parents=True)

    commands: list[list[str]] = []

    with (
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.any_path_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.working_tree",
            return_value=_make_worktree_mock(clone_path),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.run_timeout",
            new=_make_fake_run_timeout(commands),
        ),
    ):
        await clean_module._clean_git(context, git_config)

    assert commands == [
        ["git", "rm", "-r", "refs/pull/42"],
        ["git", "commit", "--amend", "--no-edit"],
        ["git", "push", "--force-with-lease", "origin", "HEAD:refs/heads/gh-pages"],
    ]


@pytest.mark.asyncio
async def test_clean_git_commit_message_when_not_amend(tmp_path: Path) -> None:
    clean_module = Clean()
    context = _make_clean_context(["42"], event_type="feature_branch")
    git_config: configuration.Git = {"branch": "gh-pages", "folder": "refs/heads/{name}", "amend": False}

    clone_path = tmp_path / "repo"
    folder_path = clone_path / "refs" / "heads" / "42"
    folder_path.mkdir(parents=True)

    commands: list[list[str]] = []

    with (
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.any_path_exists",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.GIT_WORKTREE_CACHE.working_tree",
            return_value=_make_worktree_mock(clone_path),
        ),
        patch(
            "github_app_geo_project.module.clean.module_utils.run_timeout",
            new=_make_fake_run_timeout(commands),
        ),
    ):
        await clean_module._clean_git(context, git_config)

    assert commands == [
        ["git", "rm", "-r", "refs/heads/42"],
        ["git", "commit", "-m", "Delete refs/heads/42 to clean feature_branch 42"],
        ["git", "push", "origin", "HEAD:refs/heads/gh-pages"],
    ]
