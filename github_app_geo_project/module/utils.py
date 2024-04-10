"""Module utility functions for the modules."""

import datetime
import re
import shlex
import subprocess  # nosec
from typing import Any, Union, cast

import github
import html_sanitizer
from ansi2html import Ansi2HTMLConverter

from github_app_geo_project import models, module


def add_output(
    context: module.ProcessContext[Any],
    title: str,
    data: list[str | models.OutputData],
    status: models.OutputStatus = models.OutputStatus.SUCCESS,
    access_type: models.AccessType = models.AccessType.PULL,
) -> int:
    """Add an output to the database."""
    output = models.Output(
        title=title,
        status=status,
        owner=context.github_project.owner if context.github_project else "camptocamp",
        repository=context.github_project.repository if context.github_project else "test",
        access_type=access_type,
        data=data,
    )
    context.session.add(output)
    context.session.commit()
    return output.id


class DashboardIssueItem:
    """The item of the dashboard issue."""

    title: str
    comment: str = ""
    checked: bool | None = None

    def __init__(self, title: str, comment: str = "", checked: bool | None = None) -> None:
        """Initialize the dashboard issue item."""
        self.title = title
        self.comment = comment
        self.checked = checked


DashboardIssueRaw = list[Union[DashboardIssueItem, str]]

_CHECK_RE = re.compile(r"- \[([ x])\] (.*)")
_COMMENT_RE = re.compile(r"^(.*)<!--(.*)-->(.*)$")


def parse_dashboard_issue(issue_data: str) -> DashboardIssueRaw:
    """Parse the dashboard issue."""
    result: DashboardIssueRaw = []
    lines = issue_data.split("\n")
    last_is_check = False
    for line in lines:
        line = line.strip()
        check_match = _CHECK_RE.match(line)
        if check_match is not None:
            checked = check_match.group(1) == "x"
            text = check_match.group(2).strip()
            comment_match = _COMMENT_RE.match(text)
            title = text
            comment = ""
            if comment_match is not None:
                comment = "" if comment_match is None else comment_match.group(2).strip()
                title = f"{comment_match.group(1).strip()} {comment_match.group(3).strip()}".strip()
            result.append(DashboardIssueItem(title, comment, checked))
            last_is_check = True
        else:
            if not last_is_check or line:
                result.append(line)
            last_is_check = False
    return result


def format_dashboard_issue(issue: DashboardIssueRaw) -> str:
    """Format the dashboard issue."""
    result = []
    last_is_check = False
    for item in issue:
        if isinstance(item, DashboardIssueItem):
            checked = "x" if item.checked else " "
            assert "\n" not in item.comment
            comment = f"<!-- {item.comment} --> " if item.comment else ""
            result.append(f"- [{checked}] {comment}{item.title}")
            last_is_check = True
        else:
            if last_is_check:
                result.append("")
                last_is_check = False
            result.append(item)
    return "\n".join(result)


class DashboardIssue:
    """Used to interact with dashboard issue checks."""

    issue: DashboardIssueRaw

    def __init__(self, data: str) -> None:
        """Initialize the dashboard issue."""
        self.issue = parse_dashboard_issue(data)

    def is_checked(self, name: str) -> bool | None:
        """Check if the check is checked."""
        for item in self.issue:
            if isinstance(item, DashboardIssueItem) and item.comment == name:
                return item.checked
        return None

    def get_title(self, name: str) -> str | None:
        """Get the title."""
        for item in self.issue:
            if isinstance(item, DashboardIssueItem) and item.comment == name:
                return item.title
        return None

    def set_check(self, name: str, checked: bool, title: str | None = None) -> bool:
        """Set the check."""
        for item in self.issue:
            if isinstance(item, DashboardIssueItem) and item.comment == name:
                item.checked = checked
                if title is not None:
                    item.title = title
                return True
        return False

    def set_title(self, name: str, title: str) -> bool:
        """Set the title."""
        for item in self.issue:
            if isinstance(item, DashboardIssueItem) and item.comment == name:
                item.title = title
                return True
        return False

    def add_check(self, name: str, title: str, checked: bool) -> None:
        """Add a check."""
        index = len(self.issue) - 1
        for issue in self.issue:
            if isinstance(issue, DashboardIssueItem) and issue.comment == name:
                return

        # Insert the check after the last check
        while index >= 0 and isinstance(self.issue[index], str):
            index -= 1
        if index < 0:
            self.issue.append(DashboardIssueItem(title, name, checked))
        else:
            self.issue.insert(index + 1, DashboardIssueItem(title, name, checked))

    def to_string(self) -> str:
        """Get the issue data."""
        return format_dashboard_issue(self.issue)

    def __str__(self) -> str:
        """Get the string representation."""
        return self.to_string()


class Message:
    """Represent a message with an optional title."""

    title: str

    def to_html(self) -> str:
        """Convert the message to HTML."""
        raise NotImplementedError

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the message to markdown."""
        raise NotImplementedError

    def to_plain_text(self) -> str:
        """Convert the message to plain text."""
        raise NotImplementedError


class HtmlMessage(Message):
    """Utility class to convert HTML messages to HTML/markdown."""

    def __init__(self, html: str, title: str = "") -> None:
        """Initialize the ANSI message."""
        self.html = html
        self.title = title

    def to_html(self) -> str:
        """Convert the ANSI message to HTML."""
        html = self.html
        if self.title:
            html = "\n".join([f"<h3>{self.title}</h3>", html])
        return html

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the ANSI message to markdown."""
        sanitizer = html_sanitizer.Sanitizer(
            {
                "tags": {"blockquote", "br"},
                "attributes": {},
                "empty": {"br"},
                "separate": set(),
                "keep_typographic_whitespace": True,
            }
        )
        markdown = cast(
            str, sanitizer.sanitize(self.html.replace("\n", " ").replace("<p>", "\n\n<p>"))
        ).strip()
        if summary:
            markdown = markdown.split("\n", 1)[0]

        if self.title and not summary:
            markdown = "\n".join(
                [
                    "<details>",
                    f"<summary>{self.title}</summary>",
                    markdown,
                    "</details>",
                ]
            )
        elif self.title:
            markdown = f"### {self.title}\n{markdown}"
        return markdown

    def to_plain_text(self) -> str:
        """Get the ANSI message."""
        if self.title:
            return f"{self.title}\n{remove_tags(self.html)}"
        return remove_tags(self.html)


class AnsiMessage(HtmlMessage):
    """Utility class to convert ANSI messages to HTML/markdown."""

    def __init__(self, ansi_message_str: str, title: str = "") -> None:
        """Initialize the ANSI message."""
        ansi_converter = Ansi2HTMLConverter(inline=True)
        html = ansi_converter.convert(ansi_message_str, full=False)
        super().__init__(html, title)
        self.title = title


def ansi_message(ansi: str, ansi_converter: Ansi2HTMLConverter | None) -> str:
    """Convert an ANSI message to HTML/style."""
    ansi_converter = ansi_converter or Ansi2HTMLConverter(inline=True)

    return ansi_converter.convert(ansi, full=False)


def ansi_proc_message(proc: subprocess.CompletedProcess[str]) -> Message:
    """
    Process the output of a subprocess for the dashboard (markdown)/HTML.

    Arguments:
    ---------
    proc: The subprocess result

    Return:
    ------
    The dashboard message, the simple error message, the style
    """
    args = []

    for arg in proc.args:
        if "x-access-token" in arg:
            args.append(re.sub(r"x-access-token:[0-9a-zA-Z_]*", "x-access-token:***", arg))
        else:
            args.append(arg)

    message = [f"Command: {shlex.join(args)}", f"Return code: {proc.returncode}"]
    ansi_converter = Ansi2HTMLConverter(inline=True)
    if proc.stdout:
        message.append("Output:")
        message.append(
            "<blockquote>"
            + ansi_message(proc.stdout.strip(), ansi_converter).replace("\n", "<br>")
            + "</blockquote>"
        )
    if proc.stderr:
        message.append("Error:")
        message.append(
            "<blockquote>"
            + ansi_message(proc.stderr.strip(), ansi_converter).replace("\n", "<br>")
            + "</blockquote>"
        )

    return HtmlMessage("<p>" + "</p>\n<p>".join(message) + "</p>")


def remove_tags(text: str) -> str:
    """Remove HTML tags."""
    sanitizer = html_sanitizer.Sanitizer(
        {
            "tags": {
                "unexisting",
            },
            "attributes": {},
            "empty": set(),
            "separate": set(),
            "keep_typographic_whitespace": True,
        }
    )
    return cast(
        str, sanitizer.sanitize(text.replace("\n", " ").replace("<p>", "\n<p>").replace("<br>", "\n"))
    ).strip()


def has_changes() -> bool:
    """Check if there are changes."""
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "diff", "--exit-code"], capture_output=True, encoding="utf-8"
    )
    return proc.returncode != 0


def create_commit(message: str) -> Message | None:
    """Do a commit."""
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "add", "--all"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        return ansi_proc_message(proc)
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "commit", f"--message={message}"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        return ansi_proc_message(proc)

    return None


def create_pull_request(
    branch: str, new_branch: str, message: str, body: str, repo: github.Repository.Repository
) -> tuple[Message | None, github.PullRequest.PullRequest | None]:
    """Create a pull request."""
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "push", "--force", "origin", new_branch],
        capture_output=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        return ansi_proc_message(proc), None

    pulls = repo.get_pulls(state="open", head=new_branch)
    if pulls.totalCount > 0:
        pull_request = pulls[0]
        # Create an issue it the pull request is open for 5 days
        if pull_request.created_at < datetime.datetime.now() - datetime.timedelta(days=5):
            issue = repo.create_issue(
                title=f"Pull request {message} is open for 5 days",
                body=f"See: #{pull_request.number}",
            )
            message = f"Pull request #{pull_request.number} is open for 5 days: #{issue.number}"
            return AnsiMessage(message), pull_request
    else:
        pull_request = repo.create_pull(
            title=message,
            body=body,
            head=new_branch,
            base=branch,
        )
        pull_request.enable_automerge(merge_method="SQUASH")
        return None, pull_request
    return None, None


def create_commit_pull_request(
    branch: str, new_branch: str, message: str, body: str, repo: github.Repository.Repository
) -> tuple[Message | None, github.PullRequest.PullRequest | None]:
    """Do a commit, then create a pull request."""
    error = create_commit(message)
    if error:
        return error, None
    return create_pull_request(branch, new_branch, message, body, repo)
