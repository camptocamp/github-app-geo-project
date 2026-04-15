"""Tests for the clean module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_app_geo_project.module.clean import Clean


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
