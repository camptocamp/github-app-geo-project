import datetime
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from github_app_geo_project.module import utils


async def _aiter(items):
    for item in items:
        yield item


def test_parse_dashboard_issue() -> None:
    issue_data = "first\n- [x] <!-- comment --> title\n- [ ] title2\n\nother"
    result = utils.parse_dashboard_issue(issue_data)
    assert len(result) == 4
    assert result[0] == "first"
    assert isinstance(result[1], utils.DashboardIssueItem)
    assert result[1].title == "title"
    assert result[1].comment == "comment"
    assert result[1].checked is True
    assert isinstance(result[2], utils.DashboardIssueItem)
    assert result[2].title == "title2"
    assert result[2].comment == ""
    assert result[2].checked is False
    assert result[3] == "other"


def test_format_dashboard_issue() -> None:
    data = [
        "first",
        utils.DashboardIssueItem("title", "comment", True),
        utils.DashboardIssueItem("title2", "", False),
        "other",
    ]
    result = utils.format_dashboard_issue(data)
    assert result == "first\n- [x] <!-- comment --> title\n- [ ] title2\n\nother"


def test_dashboard_issue() -> None:
    issue_data = "first\n- [x] <!-- comment --> title\n- [ ] title2\n\nother"
    dashboard_issue = utils.DashboardIssue(issue_data)

    # Test is_checked
    assert dashboard_issue.is_checked("comment") is True
    assert dashboard_issue.is_checked("nonexistent") is None

    # Test get_title
    assert dashboard_issue.get_title("comment") == "title"
    assert dashboard_issue.get_title("nonexistent") is None

    # Test set_check
    assert dashboard_issue.set_check("comment", False) is True
    assert dashboard_issue.is_checked("comment") is False
    assert dashboard_issue.set_check("nonexistent", False) is False

    # Test set_title
    assert dashboard_issue.set_title("comment", "new title") is True
    assert dashboard_issue.get_title("comment") == "new title"
    assert dashboard_issue.set_title("nonexistent", "new title") is False

    # Test add_check
    dashboard_issue.add_check("new", "new title", True)
    assert dashboard_issue.is_checked("new") is True
    assert dashboard_issue.get_title("new") == "new title"

    # Test to_string and __str__
    assert dashboard_issue.to_string() == str(dashboard_issue)

    assert (
        dashboard_issue.to_string()
        == "first\n- [ ] <!-- comment --> new title\n- [ ] title2\n- [x] <!-- new --> new title\n\nother"
    )


def test_ProcMessage() -> None:
    proc = Mock()
    proc.args = ["command", "arg1", "arg2", "x-access-token:123456"]
    proc.returncode = 0
    proc.stdout = "stdout\nmessage"
    proc.stderr = "stderr\nmessage"
    proc_message = utils.AnsiProcessMessage(proc.args, proc.returncode, proc.stdout, proc.stderr)

    assert proc_message.args == ["command", "arg1", "arg2", "x-access-token:***"]
    assert proc_message.returncode == 0
    assert "stdout\nmessage" in proc_message.stdout
    assert "stderr\nmessage" in proc_message.stderr

    markdown = proc_message.to_markdown()
    assert (
        markdown
        == """Command: command arg1 arg2 'x-access-token:***'
Return code: 0

Output:
```
stdout
message
```

Error:
```
stderr
message
```"""
    )


def test_AnsiMessage() -> None:
    ansi_message = utils.AnsiMessage("title\nmessage")

    markdown = ansi_message.to_markdown()
    assert markdown == "title\nmessage"


def test_html_to_markdown() -> None:
    html = """<span style="font-weight: bold">bold</span>
<span style="font-style: italic">italic</span></p>
<span style="font-weight: bold; color: rgb(0, 0, 255)">blue</span>
<span style="font-style: italic; color: rgb(255, 0, 0)">red</span>"""
    expected = """**bold**
*italic*
**blue**
*red*"""
    assert utils.html_to_markdown(html) == expected


def test_ansi_process_message() -> None:
    ansi_message = utils.AnsiProcessMessage(["command"], 0, "stdout\nmessage", "stderr\nmessage")
    expected_text = """Command: command
Return code: 0

Output:
```
stdout
message
```

Error:
```
stderr
message
```"""

    text = str(ansi_message)
    assert text == expected_text

    text = ansi_message.to_plain_text()
    assert text == expected_text

    markdown = ansi_message.to_markdown()
    assert markdown == expected_text

    html = ansi_message.to_html()
    assert html == (
        "<p>Command: command</p>"
        "<p>Return code: 0</p>"
        "<p>Output:</p>"
        """<pre>stdout
message</pre>"""
        "<p>Error:</p>"
        """<pre>stderr
message</pre>"""
    )


def test_manage_updated_separated():
    updated = {
        "key2": datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=23),
        "key3": datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=25),
    }
    data = {
        "key1": {},
        "key2": {},
        "key3": {},
    }
    key = "key4"
    days_old = 1

    utils.manage_updated_separated(updated, data, key, days_old)

    assert key in updated
    assert updated[key] >= datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    assert updated[key] <= datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=1)
    assert key not in data

    assert "key1" not in updated
    assert "key1" not in data

    assert "key2" in updated
    assert "key2" in data

    assert "key3" not in updated
    assert "key3" not in data


@pytest.mark.asyncio
async def test_close_pull_request_issues_close_matching_issue() -> None:
    github_project = MagicMock()
    github_project.owner = "owner"
    github_project.repository = "repo"
    github_project.application.slug = "my-app"

    github_project.aio_github.rest.pulls.async_list = AsyncMock(return_value=MagicMock(parsed_data=[]))
    github_project.aio_github.rest.git.async_delete_ref = AsyncMock()

    issue_to_close = MagicMock()
    issue_to_close.title = "Pull request Audit Snyk check/fix 1.2 is open for 14 days"
    issue_to_close.number = 101
    issue_other = MagicMock()
    issue_other.title = "Unrelated issue"
    issue_other.number = 202

    github_project.aio_github.paginate = MagicMock(return_value=_aiter([issue_to_close, issue_other]))
    github_project.aio_github.rest.issues.async_update = AsyncMock()

    await utils.close_pull_request_issues("ghci/audit/snyk/1.2", "Audit Snyk check/fix 1.2", github_project)

    github_project.aio_github.rest.issues.async_update.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=101,
        state="closed",
    )


@pytest.mark.asyncio
async def test_close_pull_request_related_issues_close_by_pull_request_number() -> None:
    github_project = MagicMock()
    github_project.owner = "owner"
    github_project.repository = "repo"
    github_project.application.slug = "my-app"

    issue_to_close = MagicMock()
    issue_to_close.title = "Pull request Audit Dpkg 2.0 is open for 9 days"
    issue_to_close.body = "See: #42\n\n[Logs](https://example.com/logs/123)"
    issue_to_close.number = 303

    issue_wrong_pr = MagicMock()
    issue_wrong_pr.title = "Pull request Audit Dpkg 2.0 is open for 9 days"
    issue_wrong_pr.body = "See: #43"
    issue_wrong_pr.number = 404

    issue_wrong_title = MagicMock()
    issue_wrong_title.title = "Audit warning"
    issue_wrong_title.body = "See: #42"
    issue_wrong_title.number = 505

    github_project.aio_github.paginate = MagicMock(
        return_value=_aiter([issue_to_close, issue_wrong_pr, issue_wrong_title]),
    )
    github_project.aio_github.rest.issues.async_update = AsyncMock()

    await utils.close_pull_request_related_issues(github_project, 42)

    github_project.aio_github.rest.issues.async_update.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=303,
        state="closed",
    )


@pytest.mark.asyncio
async def test_close_pull_request_related_issues_close_by_pull_request_title() -> None:
    github_project = MagicMock()
    github_project.owner = "owner"
    github_project.repository = "repo"
    github_project.application.slug = "my-app"

    issue_to_close = MagicMock()
    issue_to_close.title = "Pull request Audit Snyk check/fix prod-2-9-advance is open for 10 days"
    issue_to_close.body = "No explicit pull request reference"
    issue_to_close.number = 606

    issue_other = MagicMock()
    issue_other.title = "Pull request Audit Snyk check/fix prod-2-10 is open for 10 days"
    issue_other.body = "No explicit pull request reference"
    issue_other.number = 707

    github_project.aio_github.paginate = MagicMock(return_value=_aiter([issue_to_close, issue_other]))
    github_project.aio_github.rest.issues.async_update = AsyncMock()

    await utils.close_pull_request_related_issues(
        github_project,
        42,
        "Audit Snyk check/fix prod-2-9-advance",
    )

    github_project.aio_github.rest.issues.async_update.assert_awaited_once_with(
        owner="owner",
        repo="repo",
        issue_number=606,
        state="closed",
    )
