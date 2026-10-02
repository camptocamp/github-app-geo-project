# Copyright (c) 2026, Camptocamp SA

"""Tests for the changelog module."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import githubkit.exception
import pytest

from github_app_geo_project.module.changelog import Changelog, _EventData


async def _aiter(items: list[Any]) -> Any:
    """Create an async iterator from a list, like the githubkit paginate method."""
    for item in items:
        yield item


def _request_failed(status_code: int) -> githubkit.exception.RequestFailed:
    """Create a RequestFailed exception with the given status code."""
    response = MagicMock()
    response.status_code = status_code
    return githubkit.exception.RequestFailed(response)


def _release(release_id: int, tag_name: str) -> MagicMock:
    """Create a mocked release."""
    release = MagicMock()
    release.id = release_id
    release.tag_name = tag_name
    release.name = tag_name
    return release


def _make_action_context(module_event_name: str) -> Mock:
    """Create a mocked GetActionContext."""
    context = Mock()
    context.module_event_name = module_event_name
    context.github_event_name = module_event_name
    context.github_event_data = {}
    context.owner = "owner"
    context.repository = "repo"
    return context


def _make_process_context(event_data: _EventData, module_config: dict[str, Any] | None = None) -> Mock:
    """Create a mocked ProcessContext with mocked GitHub clients."""
    context = Mock()
    context.module_event_name = "event"
    context.module_event_data = event_data
    context.module_config = module_config if module_config is not None else {}
    context.github_event_data = {}
    context.github_project = Mock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"

    github = MagicMock()
    context.github_project.aio_github = github
    github.rest = MagicMock()
    github.rest.repos = AsyncMock()
    github.rest.git = AsyncMock()
    github.rest.issues = AsyncMock()
    github.rest.paginate = MagicMock(return_value=_aiter([]))
    return context


def test_get_actions_create_tag() -> None:
    """A created tag generates a tag action."""
    changelog = Changelog()
    context = _make_action_context("create")
    event = Mock()
    event.ref_type = "tag"
    event.ref = "1.0.0"

    with patch("githubkit.webhooks.parse_obj", return_value=event) as parse_obj:
        actions = changelog.get_actions(context)

    parse_obj.assert_called_once_with("create", context.github_event_data)
    assert [action.data for action in actions] == [_EventData(type="tag", version="1.0.0")]


def test_get_actions_delete_tag() -> None:
    """A deleted tag generates a tag-delete action, and not a tag action."""
    changelog = Changelog()
    context = _make_action_context("delete")
    event = Mock()
    event.ref_type = "tag"
    event.ref = "1.0.0"

    with patch("githubkit.webhooks.parse_obj", return_value=event) as parse_obj:
        actions = changelog.get_actions(context)

    parse_obj.assert_called_once_with("delete", context.github_event_data)
    assert [action.data for action in actions] == [_EventData(type="tag-delete", version="1.0.0")]


def test_get_actions_delete_branch() -> None:
    """A deleted branch is ignored."""
    changelog = Changelog()
    context = _make_action_context("delete")
    event = Mock()
    event.ref_type = "branch"
    event.ref = "feature"

    with patch("githubkit.webhooks.parse_obj", return_value=event):
        actions = changelog.get_actions(context)

    assert actions == []


def test_get_actions_release_created() -> None:
    """A created release generates a changelog action on its tag."""
    changelog = Changelog()
    context = _make_action_context("release")
    event = Mock()
    event.action = "created"
    event.release.tag_name = "1.0.0"

    with patch("githubkit.webhooks.parse_obj", return_value=event):
        actions = changelog.get_actions(context)

    assert [action.data for action in actions] == [_EventData(version="1.0.0")]


def test_get_actions_release_deleted() -> None:
    """A deleted release is ignored."""
    changelog = Changelog()
    context = _make_action_context("release")
    event = Mock()
    event.action = "deleted"
    event.release.tag_name = "1.0.0"

    with patch("githubkit.webhooks.parse_obj", return_value=event):
        actions = changelog.get_actions(context)

    assert actions == []


def test_event_data_json() -> None:
    """The event data is serialized without the None values and the old payloads are still loaded."""
    changelog = Changelog()

    assert changelog.event_data_to_json(_EventData(type="tag-delete", version="1.0.0")) == {
        "type": "tag-delete",
        "version": "1.0.0",
    }
    assert changelog.event_data_to_json(_EventData(version="1.0.0")) == {"version": "1.0.0"}
    assert changelog.event_data_from_json({"type": "tag", "version": "1.0.0"}) == _EventData(
        type="tag",
        version="1.0.0",
    )
    assert changelog.event_data_from_json({"version": "1.0.0"}) == _EventData(version="1.0.0")
    assert changelog.event_data_from_json({"type": "discussion"}) == _EventData(type="discussion")


@pytest.mark.asyncio
async def test_process_tag_delete_deletes_the_releases_of_the_tag() -> None:
    """The releases of a deleted tag are deleted, and nothing is created."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag-delete", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_release(12, "1.0.0"), _release(11, "0.9.0")]))

    output = await changelog.process(context)

    rest.repos.async_delete_release.assert_awaited_once_with(owner="owner", repo="repo", release_id=12)
    rest.repos.async_create_release.assert_not_awaited()
    rest.repos.async_update_release.assert_not_awaited()
    rest.git.async_get_ref.assert_not_awaited()
    assert output.actions == []


@pytest.mark.asyncio
async def test_process_tag_delete_tolerates_an_already_deleted_release() -> None:
    """A release already deleted by GitHub doesn't fail the job."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag-delete", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_release(12, "1.0.0")]))
    rest.repos.async_delete_release.side_effect = _request_failed(404)

    await changelog.process(context)

    rest.repos.async_delete_release.assert_awaited_once()
    rest.repos.async_create_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_tag_delete_fails_on_unexpected_error() -> None:
    """An unexpected error while deleting a release is not hidden."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag-delete", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_release(12, "1.0.0")]))
    rest.repos.async_delete_release.side_effect = _request_failed(500)

    with pytest.raises(githubkit.exception.RequestFailed):
        await changelog.process(context)


@pytest.mark.asyncio
async def test_process_tag_never_recreates_a_deleted_tag() -> None:
    """A queued tag job doesn't create the release, and then the tag, of a deleted tag."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.git.async_get_ref.side_effect = _request_failed(404)

    output = await changelog.process(context)

    rest.git.async_get_ref.assert_awaited_once_with(owner="owner", repo="repo", ref="tags/1.0.0")
    rest.repos.async_create_release.assert_not_awaited()
    rest.repos.async_update_release.assert_not_awaited()
    rest.repos.async_get_release_by_tag.assert_not_awaited()
    assert output.actions == []


@pytest.mark.asyncio
async def test_process_tag_disabled_release_creation() -> None:
    """No release is created when the create-release option is disabled."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag", version="1.0.0"), {"create-release": False})
    rest = context.github_project.aio_github.rest

    await changelog.process(context)

    rest.git.async_get_ref.assert_not_awaited()
    rest.repos.async_create_release.assert_not_awaited()
    rest.repos.async_update_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_tag_creates_the_release() -> None:
    """The release of a new tag is created and the changelog generation is queued."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.repos.async_get_release_by_tag.side_effect = _request_failed(404)
    rest.repos.async_get_latest_release.return_value = MagicMock(
        parsed_data=MagicMock(tag_name="0.9.0"),
    )

    output = await changelog.process(context)

    rest.repos.async_create_release.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        data={
            "tag_name": "1.0.0",
            "name": "1.0.0",
            "body": "",
            "make_latest": "true",
        },
    )
    rest.repos.async_update_release.assert_not_awaited()
    assert [action.data for action in output.actions] == [_EventData(version="1.0.0")]
    assert output.actions[0].title == "1.0.0"


@pytest.mark.asyncio
async def test_process_tag_updates_the_existing_release() -> None:
    """An existing release is updated in place of creating a new one."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.repos.async_get_release_by_tag.return_value = MagicMock(parsed_data=_release(12, "1.0.0"))
    rest.repos.async_get_latest_release.return_value = MagicMock(
        parsed_data=MagicMock(tag_name="2.0.0"),
    )

    await changelog.process(context)

    rest.repos.async_update_release.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        release_id=12,
        data={
            "name": "1.0.0",
            "body": "",
            "make_latest": "false",
        },
    )
    rest.repos.async_create_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_tag_create_release_already_exists() -> None:
    """A release created in parallel by an other job is updated in place of failing."""
    changelog = Changelog()
    context = _make_process_context(_EventData(type="tag", version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.repos.async_get_release_by_tag.side_effect = [
        _request_failed(404),
        MagicMock(parsed_data=_release(12, "1.0.0")),
    ]
    rest.repos.async_create_release.side_effect = _request_failed(422)
    rest.repos.async_get_latest_release.return_value = MagicMock(
        parsed_data=MagicMock(tag_name="2.0.0"),
    )

    await changelog.process(context)

    rest.repos.async_create_release.assert_awaited_once()
    rest.repos.async_update_release.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        release_id=12,
        data={
            "name": "1.0.0",
            "body": "",
            "make_latest": "false",
        },
    )
