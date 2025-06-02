from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import githubkit.versions.latest.models
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.pull_request import links, links_configuration

_CONFIG: links_configuration.PullRequestAddLinksConfiguration = {
    "content": [
        {
            "text": "Link to Jira: {project}",
            "url": "https://jira.example.com/browse/{issue}",
            "requires": ["project", "issue"],
        },
        {
            "text": "PR: {pull_request_number}",
        },
    ],
    "blacklist": {"project": ["ABC"]},
    "uppercase": ["project"],
    "branch-patterns": [
        "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$",
        "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$",
    ],
}


def create_mock_context(pull_request, module_config=None):
    """Create a mock context for testing."""
    # Create a mock ProcessContext
    context = MagicMock(spec=module.ProcessContext)
    context.module_config = module_config or _CONFIG
    context.module_event_data = {"pull-request-number": pull_request.number}

    # Mock GitHubKit API response
    github_project = MagicMock()
    context.github_project = github_project
    github_project.owner = "owner"
    github_project.repository = "repository"

    # Mock GitHubKit client
    aio_github = MagicMock()
    github_project.aio_github = aio_github
    rest = MagicMock()
    aio_github.rest = rest

    # Mock pulls endpoint
    pulls = AsyncMock()
    rest.pulls = pulls

    # Mock get response with our pull request
    get_response = MagicMock()
    get_response.parsed_data = pull_request
    pulls.async_get.return_value = get_response

    # Mock the async_update method
    update_response = MagicMock()
    pulls.async_update = AsyncMock(return_value=update_response)

    return context


def get_pull_request_mock() -> githubkit.versions.latest.models.PullRequest:
    """Create a mock PullRequest for testing."""
    pull_request = MagicMock(spec=githubkit.versions.latest.models.PullRequest)
    pull_request.body = "Description of the pull request."
    pull_request.head = MagicMock(spec=githubkit.versions.latest.models.PullRequestPropHead)
    pull_request.head.ref = "feature/branch-name"
    pull_request.number = 1

    return pull_request


@pytest.mark.asyncio
async def test_links_already_added() -> None:
    pull_request = get_pull_request_mock()
    pull_request.body = "<!-- pull request links -->"

    context = create_mock_context(pull_request)

    result = await links._add_issue_link(context)

    assert result == "Pull request links already added."
    # Verify that async_update was not called
    assert not context.github_project.aio_github.rest.pulls.async_update.called


@pytest.mark.asyncio
async def test_empty_configuration() -> None:
    pull_request = get_pull_request_mock()
    pull_request.body = ""

    context = create_mock_context(pull_request, {"content": []})

    result = await links._add_issue_link(context)

    assert result == "Empty configuration."
    # Verify that async_update was not called
    assert not context.github_project.aio_github.rest.pulls.async_update.called


@pytest.mark.asyncio
async def test_nothing_to_add() -> None:
    pull_request = get_pull_request_mock()

    context = create_mock_context(
        pull_request,
        {
            "content": [
                {
                    "text": "Link to Jira: {project}",
                    "url": "https://jira.example.com/browse/{issue}",
                    "requires": ["jira_link", "issue"],
                },
            ],
            "blacklist": {"project": ["ABC"]},
            "uppercase": ["project"],
            "branch-patterns": [
                "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$",
                "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$",
            ],
        },
    )

    result = await links._add_issue_link(context)

    assert result == "Nothing to add."
    # Verify that async_update was not called
    assert not context.github_project.aio_github.rest.pulls.async_update.called


@pytest.mark.asyncio
async def test_add_issue_link() -> None:
    pull_request = get_pull_request_mock()

    context = create_mock_context(pull_request)

    result = await links._add_issue_link(context)

    assert result == "Pull request descriptions updated."
    # Verify async_update was called with the correct parameters
    context.github_project.aio_github.rest.pulls.async_update.assert_called_once_with(
        owner=context.github_project.owner,
        repo=context.github_project.repository,
        pull_number=pull_request.number,
        body="Description of the pull request.\n\n<!-- pull request links -->\nPR: 1",
    )


@pytest.mark.asyncio
async def test_add_issue_link_2() -> None:
    pull_request = get_pull_request_mock()
    pull_request.head.ref = "feature/branch-name-def-123"

    context = create_mock_context(pull_request)

    result = await links._add_issue_link(context)

    assert result == "Pull request descriptions updated."
    # Verify async_update was called with the correct parameters
    context.github_project.aio_github.rest.pulls.async_update.assert_called_once_with(
        owner=context.github_project.owner,
        repo=context.github_project.repository,
        pull_number=pull_request.number,
        body="\n".join(
            [
                "Description of the pull request.",
                "",
                "<!-- pull request links -->",
                "[Link to Jira: DEF](https://jira.example.com/browse/123)",
                "PR: 1",
            ],
        ),
    )


@pytest.mark.asyncio
async def test_add_issue_link_with_blacklist() -> None:
    pull_request = get_pull_request_mock()
    pull_request.head.ref = "feature/branch-name-abc-123"

    context = create_mock_context(pull_request)

    result = await links._add_issue_link(context)

    assert result == "Pull request descriptions updated."
    # Verify async_update was called with the correct parameters
    context.github_project.aio_github.rest.pulls.async_update.assert_called_once_with(
        owner=context.github_project.owner,
        repo=context.github_project.repository,
        pull_number=pull_request.number,
        body="Description of the pull request.\n\n<!-- pull request links -->\nPR: 1",
    )


@pytest.mark.asyncio
async def test_links_process() -> None:
    """Test that the Links module process method works correctly."""
    # Create a mock context
    pull_request = get_pull_request_mock()
    context = create_mock_context(pull_request)

    # Mock the _add_issue_link function
    with mock.patch(
        "github_app_geo_project.module.pull_request.links._add_issue_link",
        new_callable=AsyncMock,
        return_value="Pull request descriptions updated.",
    ) as mock_add_issue_link:
        # Create the Links module instance
        links_module = links.Links()

        # Call the process method
        result = await links_module.process(context)

        # Check that _add_issue_link was called with the context
        mock_add_issue_link.assert_called_once_with(context)

        # Verify the output structure
        assert result is not None
        assert isinstance(result, module.ProcessOutput)
        assert result.output == {"summary": "Pull request descriptions updated."}


@pytest.mark.asyncio
async def test_links_get_actions() -> None:
    """Test that the Links module get_actions method works correctly for pull request events."""
    # Create a mock context
    context = MagicMock()
    context.event_name = "pull_request"

    # Create a mock pull request event
    pull_request = MagicMock()
    pull_request.number = 123
    event_data = MagicMock()
    event_data.action = "opened"
    event_data.pull_request = pull_request

    # Mock the parse_obj function to return our event_data
    with mock.patch(
        "githubkit.webhooks.parse_obj",
        return_value=event_data,
    ):
        context.event_data = {"pull_request": pull_request}

        # Create the Links module instance
        links_module = links.Links()

        # Call the get_actions method
        actions = links_module.get_actions(context)

        # Check that we get the expected action
        assert len(actions) == 1
        assert actions[0].data == {"pull-request-number": 123}
        assert actions[0].priority == module.PRIORITY_STATUS
