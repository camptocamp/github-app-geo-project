# Copyright (c) 2026, Camptocamp SA

"""Tests for the changelog module."""

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import githubkit.exception
import pytest

from github_app_geo_project.module.changelog import (
    Changelog,
    Tag,
    _EventData,
    _previous_tag,
    generate_changelog,
)
from github_app_geo_project.module.changelog import configuration as changelog_configuration


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


def _tag(name: str, sha: str) -> MagicMock:
    """Create a mocked Git tag."""
    tag = MagicMock()
    tag.name = name
    tag.commit.sha = sha
    return tag


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
    github.rest.pulls = AsyncMock()
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


@pytest.mark.asyncio
async def test_process_changelog_never_creates_a_release_when_disabled() -> None:
    """The changelog generation doesn't create a release when create-release is disabled."""
    changelog = Changelog()
    context = _make_process_context(
        _EventData(version="1.0.0"),
        {"create-release": False},
    )
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_tag("1.0.0", "abc")]))
    rest.repos.async_get_commit.return_value = MagicMock(parsed_data=MagicMock())
    rest.repos.async_get_release_by_tag.side_effect = _request_failed(404)

    with patch(
        "github_app_geo_project.module.changelog.generate_changelog",
        new=AsyncMock(return_value="body"),
    ):
        output = await changelog.process(context)

    rest.repos.async_create_release.assert_not_awaited()
    rest.repos.async_update_release.assert_not_awaited()
    assert output.actions == []


@pytest.mark.asyncio
async def test_process_changelog_updates_the_existing_release_when_disabled() -> None:
    """An existing release still gets its changelog updated when create-release is disabled."""
    changelog = Changelog()
    context = _make_process_context(
        _EventData(version="1.0.0"),
        {"create-release": False},
    )
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_tag("1.0.0", "abc")]))
    rest.repos.async_get_commit.return_value = MagicMock(parsed_data=MagicMock())
    rest.repos.async_get_release_by_tag.return_value = MagicMock(parsed_data=_release(12, "1.0.0"))

    with patch(
        "github_app_geo_project.module.changelog.generate_changelog",
        new=AsyncMock(return_value="body"),
    ):
        await changelog.process(context)

    rest.repos.async_update_release.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        release_id=12,
        data={
            "name": "1.0.0",
            "body": "body",
            "make_latest": "false",
        },
    )
    rest.repos.async_create_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_changelog_creates_the_missing_release_when_enabled() -> None:
    """The changelog generation creates the missing release when create-release is enabled."""
    changelog = Changelog()
    context = _make_process_context(_EventData(version="1.0.0"))
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(return_value=_aiter([_tag("1.0.0", "abc")]))
    rest.repos.async_get_commit.return_value = MagicMock(parsed_data=MagicMock())
    rest.repos.async_get_release_by_tag.side_effect = _request_failed(404)

    with patch(
        "github_app_geo_project.module.changelog.generate_changelog",
        new=AsyncMock(return_value="body"),
    ):
        await changelog.process(context)

    rest.repos.async_create_release.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        data={
            "tag_name": "1.0.0",
            "name": "1.0.0",
            "body": "body",
        },
    )
    rest.repos.async_update_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_changelog_gets_the_commit_of_the_requested_tag_only() -> None:
    """The commit is fetched for the tag of the release only, and not for every tag of the repository."""
    changelog = Changelog()
    context = _make_process_context(_EventData(version="1.0.1"))
    rest = context.github_project.aio_github.rest
    rest.paginate = MagicMock(
        return_value=_aiter(
            [
                _tag("1.0.0", "aaa"),
                _tag("archive/2.2", "bbb"),
                _tag("1.0.1rc1", "ccc"),
                _tag("1.0.1", "ddd"),
            ],
        ),
    )
    rest.repos.async_get_commit.return_value = MagicMock(parsed_data=MagicMock())
    rest.repos.async_get_release_by_tag.return_value = MagicMock(parsed_data=_release(12, "1.0.1"))

    with patch(
        "github_app_geo_project.module.changelog.generate_changelog",
        new=AsyncMock(return_value="body"),
    ) as mocked_generate:
        await changelog.process(context)

    rest.repos.async_get_commit.assert_awaited_once_with("owner", "repo", "ddd")
    # The invalid tag is skipped, and the pre-release is no more confused with the release
    tags = mocked_generate.await_args.args[3]
    assert sorted(tag.tag_str for tag in tags) == ["1.0.0", "1.0.1", "1.0.1rc1"]
    assert tags[Tag("1.0.1")].commit is not None
    assert tags[Tag("1.0.1rc1")].commit is None


def test_tag_pre_releases_are_distinct_tags() -> None:
    """A pre-release tag is not confused with the release tag of the same version."""
    assert Tag("2.4.0") != Tag("2.4.0rc1")
    assert Tag("2.4.0") != Tag("2.4.0.dev18")
    assert Tag("2.4.0") != Tag("2.4.0-dev.13")
    assert Tag("2.4.0-dev.13") != Tag("2.4.0-dev.19")
    assert len({Tag("2.4.0"), Tag("2.4.0rc1"), Tag("2.4.0.dev18"), Tag("2.4.0-dev.13")}) == 4


def test_tag_tags_with_more_segments_are_distinct_tags() -> None:
    """A tag with more than three segments is not confused with its three first segments."""
    assert Tag("1.4.5") != Tag("1.4.5.1")
    assert Tag("1.4.5") < Tag("1.4.5.1")
    assert (Tag("1.4.5.1").major, Tag("1.4.5.1").minor, Tag("1.4.5.1").patch) == (1, 4, 5)


def test_tag_versions_are_ordered() -> None:
    """The tags are ordered by their full version, a pre-release before its release."""
    assert Tag("2.4.0.dev13") < Tag("2.4.0rc1") < Tag("2.4.0") < Tag("2.4.1")
    assert Tag("2.9.7") < Tag("2.10.0")
    assert Tag("2.4.0") > Tag("2.4.0rc1")
    assert min([Tag("2.4.0"), Tag("2.4.0rc1"), Tag("2.4.0.dev13")]) == Tag("2.4.0.dev13")


def test_tag_legacy_release_tags() -> None:
    """The legacy release<major> tags are parsed as <major>.0.0."""
    assert (Tag("release1").major, Tag("release1").minor, Tag("release1").patch) == (1, 0, 0)
    assert Tag("release_2").version == Tag("2.0.0").version
    assert Tag("release1") == Tag("1.0.0")


def test_tag_v_prefix() -> None:
    """A leading v is ignored, and the tag name is kept as it is."""
    assert Tag("v1.2.3") == Tag("1.2.3")
    assert Tag("v1.2.3").tag_str == "v1.2.3"


@pytest.mark.parametrize(
    "tag_str",
    ["archive/2.2", "test123", "getitfixed-2.4.10", "2.4", "2.4.0-foo", ""],
)
def test_tag_invalid_tags(tag_str: str) -> None:
    """The tags that are not versions are still rejected."""
    with pytest.raises(ValueError, match="Invalid tag"):
        Tag(tag_str)


def _tags_map(*names: str) -> dict[Tag, Tag]:
    """Create a tags mapping, with a mocked commit date for every tag."""
    tags: dict[Tag, Tag] = {}
    for name in names:
        commit = MagicMock()
        commit.commit.author.date = datetime(2026, 1, 2)
        tag = Tag(tag_str=name, commit=commit)
        tags[tag] = tag
    return tags


@pytest.mark.parametrize(
    ("tag_str", "names", "expected"),
    [
        # The `.0` of the previous version line is the stabilization branch creation tag: the
        # later patches of that line are backports, they are not in the next version history.
        ("2.9.0", ["2.9.0", "2.8.0", "2.8.1"], "2.8.0"),
        ("3.0.0", ["3.0.0", "2.9.0", "2.9.7", "2.8.0", "2.8.3"], "2.9.0"),
        ("3.0.0", ["3.0.0", "2.9.7", "2.10.0.dev3", "2.10.0"], "2.10.0"),
        # The previous patch of the same version line
        ("2.8.5", ["2.8.5", "2.8.4", "2.8.0"], "2.8.4"),
        ("2.8.5", ["2.8.5", "2.8.3", "2.8.0"], "2.8.3"),
        ("2.8.0", ["2.8.0", "2.7.0", "2.7.4"], "2.7.0"),
        # The pre-releases are never used as a base
        ("3.0.0", ["3.0.0", "2.9.0rc1", "2.9.0", "2.9.7"], "2.9.0"),
        ("2.4.0", ["2.4.0", "2.3.4", "2.3.0", "2.3.0.dev3"], "2.3.0"),
        ("3.2.0rc1", ["3.2.0rc1", "3.1.0", "3.1.4"], "3.1.0"),
        # No previous release
        ("1.0.0", ["1.0.0"], None),
        ("2.0.0", ["2.0.0", "2.0.0rc1", "1.9.0rc2"], None),
    ],
)
def test_previous_tag(tag_str: str, names: list[str], expected: str | None) -> None:
    """The base of the comparison is the `.0` of the previous version line, never a pre-release."""
    previous = _previous_tag(Tag(tag_str), _tags_map(*names))
    assert (previous.tag_str if previous is not None else None) == expected


_CHANGELOG_CONFIG: changelog_configuration.Changelog = {
    "sections": [{"name": "other", "title": "Other changes"}],
    "default-section": "other",
    "routing": [],
}


def _github_project(milestone_title: str) -> Mock:
    """Create a mocked GithubProject with the GitHub clients used by generate_changelog."""
    github_project = Mock()
    github_project.owner = "owner"
    github_project.repository = "repo"
    github = MagicMock()
    github_project.aio_github = github
    github.rest.repos = AsyncMock()
    github.rest.pulls = AsyncMock()
    github.rest.issues = AsyncMock()
    # No announcement discussion
    github.graphql.arequest = AsyncMock(return_value={"data": {}})
    milestone = MagicMock()
    milestone.number = 7
    milestone.title = milestone_title
    milestone.description = None
    github.rest.paginate = MagicMock(return_value=_aiter([milestone]))
    return github_project


def _commit(sha: str, message: str = "Direct commit") -> MagicMock:
    """Create a mocked commit of the comparison."""
    commit = MagicMock()
    commit.sha = sha
    commit.commit.message = message
    commit.author = None
    commit.files = []
    return commit


def _pull_request(
    number: int,
    title: str,
    *,
    merged_at: str | None = "2026-01-01T00:00:00Z",
    state: str = "closed",
) -> MagicMock:
    """Create a mocked pull request associated with a commit."""
    pull_request = MagicMock()
    pull_request.number = number
    pull_request.title = title
    pull_request.state = state
    pull_request.merged_at = merged_at
    pull_request.milestone = None
    pull_request.labels = []
    pull_request.head.ref = f"branch-{number}"
    pull_request.user.login = f"user{number}"
    pull_request.user.html_url = f"https://github.com/user{number}"
    return pull_request


async def _generate(
    commits: list[MagicMock],
    pull_requests: dict[str, list[MagicMock]],
    *,
    tag_str: str = "1.0.1",
    tags: dict[Tag, Tag] | None = None,
) -> tuple[str, Mock]:
    """Generate a changelog with mocked GitHub answers, and return the body and the project."""
    github_project = _github_project(tag_str)
    rest = github_project.aio_github.rest
    rest.repos.async_compare_commits.return_value = MagicMock(parsed_data=MagicMock(commits=commits))
    rest.repos.async_list_pull_requests_associated_with_commit.side_effect = lambda owner, repo, sha: (
        MagicMock(parsed_data=pull_requests.get(sha, []))
    )
    rest.pulls.async_list_commits.return_value = MagicMock(parsed_data=[])
    rest.pulls.async_list_files.return_value = MagicMock(parsed_data=[])
    body = await generate_changelog(
        github_project,
        _CHANGELOG_CONFIG,
        tag_str,
        _tags_map("1.0.0", "1.0.1rc1", "1.0.1") if tags is None else tags,
    )
    return body, github_project


@pytest.mark.asyncio
async def test_generate_changelog_skips_the_unmerged_pull_requests() -> None:
    """
    The open pull requests of a stabilization branch are ignored.

    GitHub also returns them for every commit that is not present in the default branch.
    """
    body, github_project = await _generate(
        [_commit("aaa")],
        {
            "aaa": [
                _pull_request(1, "Merged change"),
                _pull_request(2, "Still open change", merged_at=None, state="open"),
            ],
        },
    )

    assert "- #1 Merged change (@user1)" in body
    assert "#2" not in body
    assert "Still open change" not in body
    # The milestone of the release is set on the merged pull request only
    github_project.aio_github.rest.issues.async_update.assert_awaited_once_with(
        "owner",
        "repo",
        1,
        data={"milestone": 7},
    )


@pytest.mark.asyncio
async def test_generate_changelog_skips_the_closed_unmerged_pull_requests() -> None:
    """A pull request closed without being merged is not part of the release, the commit is."""
    body, github_project = await _generate(
        [_commit("aaa", "Committed directly on the branch")],
        {"aaa": [_pull_request(3, "Abandoned change", merged_at=None)]},
    )

    assert "#3" not in body
    assert "Abandoned change" not in body
    # The commit is reported by itself, and not through the unmerged pull request
    assert "- aaa Committed directly on the branch ()" in body
    github_project.aio_github.rest.issues.async_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_changelog_reports_the_commits_without_pull_request() -> None:
    """A commit that is not associated with any pull request is reported by itself."""
    body, _ = await _generate([_commit("aaa", "Committed directly on the branch")], {})

    assert "- aaa Committed directly on the branch ()" in body


@pytest.mark.asyncio
async def test_generate_changelog_compares_the_requested_tag() -> None:
    """The comparison uses the requested tag, and not an homonym pre-release of it."""
    _, github_project = await _generate(
        [_commit("aaa")],
        {},
        tags=_tags_map("1.0.0", "1.0.1", "1.0.1rc1"),
    )

    github_project.aio_github.rest.repos.async_compare_commits.assert_awaited_once_with(
        "owner",
        "repo",
        "1.0.0...1.0.1",
    )
