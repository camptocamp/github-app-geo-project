from unittest import mock

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


def get_pull_request_mock() -> mock.Mock:
    pull_request = mock.Mock()
    pull_request.body = "Description of the pull request."
    pull_request.head.ref = "feature/branch-name"
    pull_request.number = "1"

    return pull_request


def test_links_already_added() -> None:
    pull_request = get_pull_request_mock()
    pull_request.body = "<!-- pull request links -->"

    result = links._add_issue_link(_CONFIG, pull_request)

    assert result == "Pull request links already added."


def test_empty_configuration() -> None:
    pull_request = get_pull_request_mock()
    pull_request.body = ""

    result = links._add_issue_link({}, pull_request)

    assert result == "Empty configuration."


def test_nothing_to_add() -> None:
    pull_request = get_pull_request_mock()

    result = links._add_issue_link(
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
        pull_request,
    )

    assert result == "Nothing to add."


def test_add_issue_link() -> None:
    pull_request = get_pull_request_mock()

    result = links._add_issue_link(_CONFIG, pull_request)

    assert result == "Pull request descriptions updated."
    assert pull_request.edit.call_args[1]["body"] == "\n".join(
        ["Description of the pull request.", "<!-- pull request links -->", "PR: 1"]
    )


def test_add_issue_link_2() -> None:
    pull_request = get_pull_request_mock()
    pull_request.head.ref = "feature/branch-name-def-123"

    result = links._add_issue_link(_CONFIG, pull_request)

    assert result == "Pull request descriptions updated."
    assert pull_request.edit.call_args[1]["body"] == "\n".join(
        [
            "Description of the pull request.",
            "<!-- pull request links -->",
            "[Link to Jira: DEF](https://jira.example.com/browse/123)",
            "PR: 1",
        ]
    )


def test_add_issue_link_with_blacklist() -> None:
    pull_request = get_pull_request_mock()
    pull_request.head.ref = "feature/branch-name-abc-123"

    result = links._add_issue_link(_CONFIG, pull_request)

    assert result == "Pull request descriptions updated."
    assert pull_request.edit.call_args[1]["body"] == "\n".join(
        ["Description of the pull request.", "<!-- pull request links -->", "PR: 1"]
    )
