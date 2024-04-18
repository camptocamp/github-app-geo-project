from unittest.mock import Mock

from github_app_geo_project.module import utils


def test_parse_dashboard_issue() -> None:
    issue_data = "first\n- [x] <!-- comment --> title\n- [ ] title2\n\nother"
    result = utils.parse_dashboard_issue(issue_data)
    assert len(result) == 4
    assert result[0] == "first"
    assert isinstance(result[1], utils.DashboardIssueItem)
    assert result[1].title == "title"
    assert result[1].comment == "comment"
    assert result[1].checked == True
    assert isinstance(result[2], utils.DashboardIssueItem)
    assert result[2].title == "title2"
    assert result[2].comment == ""
    assert result[2].checked == False
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
    assert dashboard_issue.is_checked("comment") == True
    assert dashboard_issue.is_checked("nonexistent") == None

    # Test get_title
    assert dashboard_issue.get_title("comment") == "title"
    assert dashboard_issue.get_title("nonexistent") == None

    # Test set_check
    assert dashboard_issue.set_check("comment", False) == True
    assert dashboard_issue.is_checked("comment") == False
    assert dashboard_issue.set_check("nonexistent", False) == False

    # Test set_title
    assert dashboard_issue.set_title("comment", "new title") == True
    assert dashboard_issue.get_title("comment") == "new title"
    assert dashboard_issue.set_title("nonexistent", "new title") == False

    # Test add_check
    dashboard_issue.add_check("new", "new title", True)
    assert dashboard_issue.is_checked("new") == True
    assert dashboard_issue.get_title("new") == "new title"

    # Test to_string and __str__
    assert dashboard_issue.to_string() == str(dashboard_issue)

    assert (
        dashboard_issue.to_string()
        == "first\n- [ ] <!-- comment --> new title\n- [ ] title2\n- [x] <!-- new --> new title\n\nother"
    )


def test_ProcMessage():
    proc = Mock()
    proc.args = ["command", "arg1", "arg2", "x-access-token:123456"]
    proc.returncode = 0
    proc.stdout = "stdout\nmessage"
    proc.stderr = "stderr\nmessage"
    proc_message = utils.ProcessMessage(proc)

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


def test_AnsiMessage():
    ansi_message = utils.AnsiMessage("title\nmessage")

    markdown = ansi_message.to_markdown()
    assert markdown == "title\nmessage"
