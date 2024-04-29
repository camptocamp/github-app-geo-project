"""Module utility functions for the modules."""

import datetime
import logging
import os
import re
import shlex
import subprocess  # nosec
from typing import Any, Union, cast

import c2cciutils.security
import github
import html_sanitizer
from ansi2html import Ansi2HTMLConverter

from github_app_geo_project import configuration, models, module

_LOGGER = logging.getLogger(__name__)


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

    def remove_check(self, name: str) -> None:
        """Remove a check."""
        for index, issue in enumerate(self.issue):
            if isinstance(issue, DashboardIssueItem) and issue.comment == name:
                self.issue.pop(index)
                return

    def to_string(self) -> str:
        """Get the issue data."""
        return format_dashboard_issue(self.issue)

    def __str__(self) -> str:
        """Get the string representation."""
        return self.to_string()


class Message:
    """Represent a message with an optional title."""

    title: str

    def to_html(self, style: str = "h3") -> str:
        """Convert the message to HTML."""
        raise NotImplementedError

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the message to markdown."""
        raise NotImplementedError

    def to_plain_text(self) -> str:
        """Convert the message to plain text."""
        raise NotImplementedError


_suffix = 0  # pylint: disable=invalid-name


class HtmlMessage(Message):
    """Utility class to convert HTML messages to HTML/markdown."""

    def __init__(self, html: str, title: str = "") -> None:
        """Initialize the ANSI message."""
        self.html = html
        self.title = title

    def to_html(self, style: str = "h3") -> str:
        """Convert the ANSI message to HTML."""
        global _suffix  # pylint: disable=global-statement

        html = self.html
        if self.title:
            _suffix += 1
            if style == "collapse":
                html = "".join(
                    [
                        '<div class="collapse-container">',
                        '<p class="d-inline-flex gap-1">',
                        "<a",
                        '  class=""',
                        '  data-bs-toggle="collapse"',
                        f'  href="#element{_suffix}"',
                        '  role="button"',
                        '  aria-expanded="false"',
                        '  aria-controls="collapseExample">',
                        '<em class="col-up bi bi-chevron-right">&nbsp;</em>',
                        '<em class="col-down bi bi-chevron-down">&nbsp;</em>',
                        self.title,
                        "</a>",
                        "</p>",
                        f'<div class="collapse" id="element{_suffix}">',
                        html,
                        "</div>",
                        "</div>",
                    ]
                )
            else:
                html = "\n".join([f"<{style}>{self.title}</{style}>", html])
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
            str,
            sanitizer.sanitize(self.html.replace("\n", " ").replace("<p>", "\n\n<p>").replace("<br>", "\n")),
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
            markdown = f"#### {self.title}\n{markdown}"
        return markdown

    def __str__(self) -> str:
        """Get the string representation."""
        return self.to_plain_text()

    def to_plain_text(self) -> str:
        """Get the ANSI message."""
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
        message = cast(
            str,
            sanitizer.sanitize(self.html.replace("<p>", "\n<p>").replace("<br>", "\n")),
        ).strip()

        if self.title:
            return f"{self.title}\n{message}"
        return message


class AnsiMessage(HtmlMessage):
    """Convert ANSI messages to HTML/markdown."""

    _ansi_converter = Ansi2HTMLConverter(inline=True)
    _markdown_sanitizer = html_sanitizer.Sanitizer(
        {
            "tags": {"unexisting"},
            "attributes": {},
            "empty": set(),
            "separate": set(),
            "keep_typographic_whitespace": True,
        }
    )

    def __init__(self, ansi_message_str: str, title: str = "", _is_html: bool = False) -> None:
        """Initialize the ANSI message."""
        html = ansi_message_str
        if not _is_html:
            ansi_converter = Ansi2HTMLConverter(inline=True)
            self.raw_html = ansi_converter.convert(ansi_message_str.strip(), full=False)
            html = self.raw_html
        super().__init__(html, title)

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the ANSI message to markdown."""
        if summary and self.title:
            return f"<details><summary>{self.title}</summary>{self._markdown_sanitizer.sanitize(self.raw_html)}</details>"
        return cast(str, self._markdown_sanitizer.sanitize(self.raw_html))

    def to_plain_text(self) -> str:
        """Get the process message."""
        return self.to_markdown()


class ProcessMessage(AnsiMessage):
    """Represent a message from a subprocess."""

    def __init__(self, proc: subprocess.CompletedProcess[str] | subprocess.CalledProcessError) -> None:
        """Initialize the process message."""
        self.args = []

        for arg in proc.args:
            if "x-access-token" in arg:
                self.args.append(re.sub(r"x-access-token:[0-9a-zA-Z_]*", "x-access-token:***", arg))
            else:
                self.args.append(arg)

        self.returncode = proc.returncode
        self.stdout = self._ansi_converter.convert(proc.stdout, full=False)
        self.stderr = self._ansi_converter.convert(proc.stderr, full=False)

        message = [f"Command: {shlex.join(self.args)}", f"Return code: {proc.returncode}"]
        if self.stdout:
            message.append("Output:")
            message.append(self.stdout)
        if self.stderr:
            message.append("Error:")
            message.append(self.stderr)

        super().__init__("".join([f"<p>{line}</p>" for line in message]), _is_html=True)

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the process message to markdown."""
        return "\n".join(
            [
                "<details>",
                f"<summary>{self.title}</summary>",
                f"Command: {shlex.join(self.args)}",
                f"Return code: {self.returncode}",
                "Output:",
                "```",
                self._markdown_sanitizer.sanitize(self.stdout).strip(),
                "```",
                "",
                "Error:",
                "```",
                self._markdown_sanitizer.sanitize(self.stderr).strip(),
                "```",
                "</details>",
            ]
            if summary and self.title
            else [
                *([f"#### {self.title}"] if self.title else []),
                f"Command: {shlex.join(self.args)}",
                f"Return code: {self.returncode}",
                "Output:",
                "```",
                self._markdown_sanitizer.sanitize(self.stdout).strip(),
                "```",
                "",
                "Error:",
                "```",
                self._markdown_sanitizer.sanitize(self.stderr).strip(),
                "```",
            ]
        )


def ansi_proc_message(proc: subprocess.CompletedProcess[str] | subprocess.CalledProcessError) -> Message:
    """
    Process the output of a subprocess for the dashboard (markdown)/HTML.

    Arguments:
    ---------
    proc: The subprocess result

    Return:
    ------
    The dashboard message, the simple error message, the style
    """
    return ProcessMessage(proc)


def has_changes(include_un_followed: bool = False) -> bool:
    """Check if there are changes."""
    if include_un_followed:
        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
            ["git", "status", "--porcelain"], capture_output=True, encoding="utf-8", timeout=30
        )
        return bool(proc.stdout)
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "diff", "--exit-code"], capture_output=True, encoding="utf-8", timeout=30
    )
    return proc.returncode != 0


def create_commit(message: str) -> bool:
    """Do a commit."""
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "add", "--all"], capture_output=True, encoding="utf-8", timeout=30
    )
    if proc.returncode != 0:
        proc_message = ansi_proc_message(proc)
        proc_message.title = "Error adding files to commit"
        _LOGGER.warning(proc_message.to_html(style="collapse"))
        return False
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "commit", f"--message={message}"], capture_output=True, encoding="utf-8", timeout=30
    )
    if proc.returncode != 0:
        proc_message = ansi_proc_message(proc)
        proc_message.title = "Error committing files"
        _LOGGER.warning(proc_message.to_html(style="collapse"))
        return False

    return True


def create_pull_request(
    branch: str, new_branch: str, message: str, body: str, repo: github.Repository.Repository
) -> tuple[bool, github.PullRequest.PullRequest | None]:
    """Create a pull request."""
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "push", "--force", "origin", new_branch],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        proc_message = ansi_proc_message(proc)
        proc_message.title = "Error pushing branch"
        _LOGGER.warning(proc_message.to_html(style="collapse"))
        return False, None

    pulls = repo.get_pulls(state="open", head=f"{repo.full_name.split('/')[0]}:{new_branch}")
    if pulls.totalCount > 0:
        pull_request = pulls[0]
        _LOGGER.debug(
            "Found pull request #%s (%s - %s)",
            pull_request.number,
            pull_request.created_at,
            pull_request.head.ref,
        )
        # Create an issue it the pull request is open for 5 days
        if pull_request.created_at < datetime.datetime.now(tz=datetime.timezone.utc) - datetime.timedelta(
            days=5
        ):
            issue = repo.create_issue(
                title=f"Pull request {message} is open for 5 days",
                body=f"See: #{pull_request.number}",
            )
            _LOGGER.warning("Pull request #%s is open for 5 days: #%s", pull_request.number, issue.number)
            return False, pull_request
    else:
        pull_request = repo.create_pull(
            title=message,
            body=body,
            head=new_branch,
            base=branch,
        )
        pull_request.enable_automerge(merge_method="SQUASH")
        return True, pull_request
    return True, None


def create_commit_pull_request(
    branch: str, new_branch: str, message: str, body: str, repo: github.Repository.Repository
) -> tuple[bool, github.PullRequest.PullRequest | None]:
    """Do a commit, then create a pull request."""
    if not create_commit(message):
        return False, None
    return create_pull_request(branch, new_branch, message, body, repo)


def git_clone(github_project: configuration.GithubProject, branch: str) -> bool:
    """Clone the Git repository."""
    # Store the ssh key
    directory = os.path.expanduser("~/.ssh/")
    if not os.path.exists(directory):
        os.makedirs(directory)
    with open(os.path.join(directory, "id_rsa"), "w", encoding="utf-8") as file:
        file.write(github_project.application.auth.private_key)

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        [
            "git",
            "clone",
            "--depth=1",
            f"--branch={branch}",
            f"https://x-access-token:{github_project.token}@github.com/{github_project.owner}/{github_project.repository}.git",
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=300,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error cloning the repository"
        _LOGGER.warning(message.to_html(style="collapse"))
        return False
    message.title = "Clone repository"
    _LOGGER.debug(message.to_html(style="collapse"))

    os.chdir(github_project.repository.split("/")[-1])
    app = github_project.application.integration.get_app()
    user = github_project.github.get_user(app.slug + "[bot]")
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        [
            "git",
            "config",
            "user.email",
            f"{user.id}+{user.login}@users.noreply.github.com",
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error setting the email"
        _LOGGER.warning(message.to_html(style="collapse"))
        return False
    message.title = "Set email"
    _LOGGER.debug(message.to_html(style="collapse"))

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "config", "user.name", user.login],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error setting the name"
        _LOGGER.warning(message.to_html(style="collapse"))
        return False
    message.title = "Set name"
    _LOGGER.debug(message.to_html(style="collapse"))

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "config", "gpg.format", "ssh"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error setting the gpg format"
        _LOGGER.warning(message.to_html(style="collapse"))
        return False
    message.title = "Set gpg format"
    _LOGGER.debug(message.to_html(style="collapse"))

    return True


def get_stabilization_branch(security: c2cciutils.security.Security) -> list[str]:
    """Get the stabilization versions."""
    alternate_index = security.headers.index("Alternate Tag") if "Alternate Tag" in security.headers else -1
    version_index = security.headers.index("Version") if "Version" in security.headers else -1
    supported_until_index = (
        security.headers.index("Supported Until") if "Supported Until" in security.headers else -1
    )

    if version_index < 0:
        _LOGGER.warning("No Version column in the SECURITY.md")
        return []
    if supported_until_index < 0:
        _LOGGER.warning("No Supported Until column in the SECURITY.md")
        return []

    alternate = []
    if alternate_index >= 0:
        for row in security.data:
            if row[alternate_index]:
                alternate.append(row[alternate_index])

    versions = []
    for row in security.data:
        if row[supported_until_index] != "Unsupported":
            if alternate:
                if row[alternate_index] not in alternate:
                    versions.append(row[version_index])
            else:
                versions.append(row[version_index])
    return versions


def manage_updated(status: dict[str, Any], key: str, days_old: int = 2) -> None:
    """
    Manage the updated status.

    Add an updated field to the status and remove the old status.
    """
    status.setdefault(key, {})["updated"] = datetime.datetime.now().isoformat()
    for other_key, other_object in list(status.items()):
        if (
            not isinstance(other_object, dict)
            or "updated" not in other_object
            or datetime.datetime.fromisoformat(other_object["updated"])
            < datetime.datetime.now() - datetime.timedelta(days=days_old)
        ):
            _LOGGER.debug(
                "Remove old status %s (%s < %s)",
                other_key,
                other_object.get("updated", "-"),
                datetime.datetime.now() - datetime.timedelta(days=days_old),
            )
            del status[other_key]
