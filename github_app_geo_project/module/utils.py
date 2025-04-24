"""Module utility functions for the modules."""

import asyncio
import datetime
import logging
import math
import os
import re
import shlex
import urllib
from pathlib import Path
from typing import Any, cast

import anyio
import githubkit.exception
import githubkit.versions.latest.models
import html_sanitizer
import markdownify
import security_md
import sqlalchemy.ext.asyncio
from ansi2html import Ansi2HTMLConverter

from github_app_geo_project import configuration, models, module, utils

_LOGGER = logging.getLogger(__name__)
WORKING_DIRECTORY_LOCK = asyncio.Lock()


async def add_output(
    context: module.ProcessContext[Any, Any],
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
        repository=(context.github_project.repository if context.github_project else "test"),
        access_type=access_type,
        data=data,
    )
    context.session.add(output)
    await context.session.commit()
    await context.session.refresh(output)
    return output.id


class DashboardIssueItem:
    """The item of the dashboard issue."""

    title: str
    comment: str = ""
    checked: bool | None = None

    def __init__(
        self,
        title: str,
        comment: str = "",
        checked: bool | None = None,
    ) -> None:
        """Initialize the dashboard issue item."""
        self.title = title
        self.comment = comment
        self.checked = checked


DashboardIssueRaw = list[DashboardIssueItem | str]

_CHECK_RE = re.compile(r"- \[([ x])\] (.*)")
_COMMENT_RE = re.compile(r"^(.*)<!--(.*)-->(.*)$")


def parse_dashboard_issue(issue_data: str) -> DashboardIssueRaw:
    """Parse the dashboard issue."""
    result: DashboardIssueRaw = []
    lines = issue_data.split("\n")
    last_is_check = False
    for line in lines:
        line = line.strip()  # noqa: PLW2901
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

_BOLD_RE = re.compile(r'<span style="font-weight: bold(;[^"]*)?">([^<]*)</span>')
_ITALIC_RE = re.compile(r'<span style="font-style: italic(;[^"]*)?">([^<]*)</span>')


def html_to_markdown(html: str) -> str:
    """Convert HTML to markdown."""
    html = _BOLD_RE.sub(r"<b>\2</b>", html)
    html = _ITALIC_RE.sub(r"<i>\2</i>", html)
    return markdownify.markdownify(html)


class HtmlMessage(Message):
    """Utility class to convert HTML messages to HTML/markdown."""

    def __init__(self, html: str, title: str = "") -> None:
        """Initialize the ANSI message."""
        self.html = html
        self.title = title

    def to_html(self, style: str = "h3") -> str:
        """Convert the ANSI message to HTML."""
        global _suffix  # noqa: PLW0603

        # interpret template parameters
        html = self.html.replace(
            "{pre}",
            "<pre>" if style != "collapse" else "",
        ).replace(
            "{post}",
            "</pre>" if style != "collapse" else "",
        )
        if self.title and style != "no-title":
            if style == "collapse":
                _suffix += 1
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
                    ],
                )
            else:
                html = "\n".join([f"<{style}>{self.title}</{style}>", html])

        sanitizer = html_sanitizer.Sanitizer(
            {
                "tags": {
                    *html_sanitizer.sanitizer.DEFAULT_SETTINGS["tags"],
                    "span",
                    "div",
                    "pre",
                    "code",
                },
                "attributes": {
                    "a": (
                        "href",
                        "name",
                        "target",
                        "title",
                        "rel",
                        "class",
                        "data-bs-toggle",
                        "role",
                        "aria-expanded",
                        "aria-controls",
                    ),
                    "span": ("class", "style"),
                    "p": ("class", "style"),
                    "div": ("class", "style", "id"),
                    "em": ("class", "style"),
                },
                "separate": {
                    *html_sanitizer.sanitizer.DEFAULT_SETTINGS["tags"],
                    "span",
                    "div",
                    "pre",
                    "code",
                },
                "keep_typographic_whitespace": True,
            },
        )
        return cast("str", sanitizer.sanitize(html))

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the ANSI message to markdown."""
        markdown = html_to_markdown(self.html)
        if summary:
            markdown = markdown.split("\n", 1)[0]

        if self.title and not summary:
            markdown = "\n".join(
                [
                    "<details>",
                    f"<summary>{self.title}</summary>",
                    markdown,
                    "</details>",
                ],
            )
        elif self.title:
            markdown = f"#### {self.title}\n{markdown}"
        return markdown

    def __str__(self) -> str:
        """Get the string representation."""
        return self.to_plain_text()

    def __repr__(self) -> str:
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
            },
        )
        message = cast(
            "str",
            sanitizer.sanitize(self.html.replace("<p>", "\n<p>").replace("<br>", "\n")),
        ).strip()

        if self.title:
            return f"{self.title}\n{message}"
        return message


class AnsiMessage(HtmlMessage):
    """Convert ANSI messages to HTML/markdown."""

    _ansi_converter = Ansi2HTMLConverter(inline=True)

    def __init__(
        self,
        ansi_message_str: str,
        title: str = "",
        _is_html: bool = False,
    ) -> None:
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
            return f"<details><summary>{self.title}</summary>{html_to_markdown(self.raw_html)}</details>"
        return html_to_markdown(self.raw_html)

    def to_plain_text(self) -> str:
        """Get the process message."""
        return self.to_markdown()


class AnsiProcessMessage(AnsiMessage):
    """Represent a message from a subprocess."""

    def __init__(
        self,
        args: list[str],
        returncode: int | None,
        stdout: str | bytes,
        stderr: str | bytes,
        error: str | None = None,
    ) -> None:
        """Initialize the process message."""
        self.args: list[str] = []

        args = [str(arg) for arg in args]

        for arg in args:
            if "x-access-token" in arg:
                self.args.append(
                    re.sub(r"x-access-token:[0-9a-zA-Z_.-]*", "x-access-token:***", arg),
                )
            else:
                self.args.append(arg)

        self.returncode = returncode
        if isinstance(stdout, bytes):
            try:
                stdout = stdout.decode()
            except UnicodeDecodeError:
                stdout = "- binary data -"
        self.stdout = self._ansi_converter.convert(stdout or "", full=False)
        if isinstance(stderr, bytes):
            try:
                stderr = stderr.decode()
            except UnicodeDecodeError:
                stderr = "- binary data -"
        self.stderr = self._ansi_converter.convert(stderr or "", full=False)

        message = [f"Command: {shlex.join(self.args)}"]
        if error:
            message.append(f"Error: {error}")
        if returncode is not None:
            message.append(f"Return code: {returncode}")
        if self.stdout.strip():
            message.append("Output:")
            message.append(f"{{pre}}{self.stdout}{{post}}")
        if self.stderr.strip():
            message.append("Error:")
            message.append(f"{{pre}}{self.stderr}{{post}}")

        super().__init__("".join([f"<p>{line}</p>" for line in message]), _is_html=True)

    def to_markdown(self, summary: bool = False) -> str:
        """Convert the process message to markdown."""
        return "\n".join(
            (
                [
                    "<details>",
                    f"<summary>{self.title}</summary>",
                    f"Command: {shlex.join(self.args)}",
                    f"Return code: {self.returncode}",
                    *(
                        [
                            "",
                            "Output:",
                            "```",
                            html_to_markdown(self.stdout.strip()),
                            "```",
                        ]
                        if self.stdout.strip()
                        else []
                    ),
                    *(
                        [
                            "",
                            "Error:",
                            "```",
                            html_to_markdown(self.stderr.strip()),
                            "```",
                        ]
                        if self.stderr.strip()
                        else []
                    ),
                    "</details>",
                ]
                if summary and self.title
                else [
                    *([f"#### {self.title}"] if self.title else []),
                    f"Command: {shlex.join(self.args)}",
                    f"Return code: {self.returncode}",
                    *(
                        [
                            "",
                            "Output:",
                            "```",
                            html_to_markdown(self.stdout.strip()),
                            "```",
                        ]
                        if self.stdout.strip()
                        else []
                    ),
                    *(
                        [
                            "",
                            "Error:",
                            "```",
                            html_to_markdown(self.stderr.strip()),
                            "```",
                        ]
                        if self.stderr.strip()
                        else []
                    ),
                ]
            ),
        )

    @staticmethod
    def from_async_artifacts(
        command: list[str],
        proc: asyncio.subprocess.Process,  # pylint: disable=no-member
        stdout: bytes | None,
        stderr: bytes | None,
    ) -> "AnsiProcessMessage":
        """Create an AnsiProcessMessage from async artifacts."""
        return AnsiProcessMessage(
            command,
            -999 if proc.returncode is None else proc.returncode,
            "" if stdout is None else stdout.decode(),
            "" if stderr is None else stderr.decode(),
        )


def get_cwd() -> Path | None:
    """
    Get the current working directory.

    Did not raise an exception if it does not exist, return None instead.
    """
    try:
        return Path.cwd()
    except FileNotFoundError:
        return None


async def run_timeout(
    command: list[str],
    env: dict[str, str] | None,
    timeout: int,  # noqa: ASYNC109
    success_message: str,
    error_message: str,
    timeout_message: str,
    cwd: Path,
    error: bool = True,
) -> tuple[str | None, bool, Message | None]:
    """
    Run a command with a timeout.

    Arguments:
    ---------
    command: The command to run
    env: The environment variables
    timeout: The timeout
    success_message: The message on success
    error_message: The message on error
    timeout_message: The message on timeout
    cwd: The working directory
    error: Set to false to don't get error log (silent mode)

    Return:
    ------
    The standard output, the success, the logged message
    """
    log_message = "Run command: %s"
    args: list[Any] = [shlex.join(command)]
    if cwd:
        log_message += ", in %s"
        args.append(cwd)
    if timeout:
        log_message += ", timeout %ds"
        args.append(timeout)
    _LOGGER.debug(log_message, *args)
    async_proc = None
    start = datetime.datetime.now(datetime.UTC)
    try:
        async with asyncio.timeout(timeout):
            try:
                async_proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await async_proc.communicate()
            finally:
                _LOGGER.debug("Command %s finished", shlex.join(command))
            assert async_proc.returncode is not None
            message: Message = AnsiProcessMessage.from_async_artifacts(
                command,
                async_proc,
                stdout,
                stderr,
            )
            success = async_proc.returncode == 0
            if success:
                message.title = f"{success_message}, in {datetime.datetime.now(datetime.UTC) - start}s."
                _LOGGER.debug(message)
            else:
                message.title = f"{error_message}, in {datetime.datetime.now(datetime.UTC) - start}s."
                _LOGGER.warning(message)
            return stdout.decode(), success, message
    except FileNotFoundError as exception:
        if error:
            _LOGGER.exception("%s not found: %s", command[0], exception)  # noqa: TRY401
        else:
            _LOGGER.warning("%s not found", command[0])
        cmd = ["find", "/", "-name", command[0]]
        proc = await asyncio.create_subprocess_exec(  # pylint: disable=subprocess-run-check
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        async with asyncio.timeout(60):
            stdout, stderr = await proc.communicate()
        message = AnsiProcessMessage.from_async_artifacts(cmd, proc, stdout, stderr)
        message.title = f"Find {command[0]}"
        _LOGGER.debug(message)
        return None, False, message
    except TimeoutError as exception:
        if async_proc:
            try:
                async_proc.kill()
            except ProcessLookupError:
                pass
            message = AnsiProcessMessage(
                command,
                None,
                ("" if async_proc.stdout is None else (await async_proc.stdout.read()).decode()),
                ("" if async_proc.stderr is None else (await async_proc.stderr.read()).decode()),
                error=str(exception),
            )
            message.title = timeout_message
            _LOGGER.warning(message)
            return None, False, message
        if error:
            _LOGGER.exception(
                "TimeoutError for %s: %s",
                command[0],
                exception,  # noqa: TRY401
            )
        else:
            _LOGGER.warning("TimeoutError for %s", command[0])
        return None, False, AnsiProcessMessage(command, None, "", "", str(exception))


async def has_changes(cwd: Path, include_un_followed: bool = False) -> bool:
    """Check if there are changes."""
    if include_un_followed:
        stdout, _, _ = await run_timeout(
            ["git", "status", "--porcelain"],
            None,
            60,
            "Git status",
            "Error getting git status",
            "Timeout getting git status",
            cwd,
        )
        return bool(stdout)
    _, success, _ = await run_timeout(
        ["git", "diff", "--exit-code"],
        None,
        60,
        "Git diff",
        "Error running git diff",
        "Timeout running git diff",
        cwd,
    )
    return not success


async def create_commit(message: str, cwd: Path) -> bool:
    """Do a commit."""
    _, success, _ = await run_timeout(
        ["git", "add", "--all"],
        None,
        30,
        "Add files to commit",
        "Error adding files to commit",
        "Timeout adding files to commit",
        cwd,
    )
    if not success:
        return False
    _, success, _ = await run_timeout(
        [
            "git",
            "commit",
            f"--message={message}",
        ],
        None,
        600,
        "Commit",
        "Error committing files",
        "Timeout committing files",
        cwd,
    )
    return success


async def create_pull_request(
    branch: str,
    new_branch: str,
    message: str,
    body: str,
    github_project: configuration.GithubProject,
    cwd: Path,
    auto_merge: bool = True,
) -> tuple[bool, githubkit.versions.latest.models.PullRequest | None]:
    """Create a pull request."""
    _, success, _ = await run_timeout(
        ["git", "push", "--force", "origin", new_branch],
        None,
        60,
        "Push branch",
        "Error pushing branch",
        "Timeout pushing branch",
        cwd,
    )
    if not success:
        return False, None
    pull_requests = (
        await github_project.aio_github.rest.pulls.async_list(
            owner=github_project.owner,
            repo=github_project.repository,
            state="open",
            head=f"{github_project.owner}:{new_branch}",
        )
    ).parsed_data
    if pull_requests:
        assert pull_requests is not None
        pull_request = pull_requests[0]
        _LOGGER.debug(
            "Found pull request #%s (%s - %s)",
            pull_request.number,
            pull_request.created_at,
            pull_request.head.ref,
        )
        # Create an issue if the pull request is open for 5 days
        if pull_request.created_at < datetime.datetime.now(
            tz=datetime.UTC,
        ) - datetime.timedelta(days=5):
            _LOGGER.warning("Pull request #%s is open for 5 days", pull_request.number)
            title = f"Pull request {message} is open for 5 days"
            body = f"See: #{pull_request.number}"
            found = False
            issues = (
                await github_project.aio_github.rest.issues.async_list_for_repo(
                    owner=github_project.owner,
                    repo=github_project.repository,
                    creator=f"{github_project.application.slug}[bot]",
                    state="open",
                )
            ).parsed_data
            if issues:
                assert issues is not None
                for issue in issues:
                    if title == issue.title:
                        found = True
                        if body != issue.body:
                            await github_project.aio_github.rest.issues.async_update(
                                owner=github_project.owner,
                                repo=github_project.repository,
                                issue_number=issue.number,
                                body=body,
                            )
            if not found:
                await github_project.aio_github.rest.issues.async_create(
                    owner=github_project.owner,
                    repo=github_project.repository,
                    title=title,
                    body=body,
                )
            return False, pull_request
    else:
        pull_request = await github_project.aio_github.rest.pulls.async_create(
            owner=github_project.owner,
            repo=github_project.repository,
            data={
                "title": message,
                "body": body,
                "head": new_branch,
                "base": branch,
            },
        )
        if auto_merge:
            await auto_merge_pull_request(github_project, pull_request.parsed_data)
        return True, pull_request.parsed_data
    return True, None


async def auto_merge_pull_request(
    github_project: configuration.GithubProject,
    pull_request: githubkit.versions.latest.models.PullRequest,
) -> None:
    """Enable the automerge of a pull request."""
    exception: Exception | None = None
    for n in range(10):
        try:
            if n != 0:
                await asyncio.sleep(math.pow(n, 2))
            await github_project.aio_github.graphql.arequest(
                """
                mutation EnableAutoMerge($pullRequestId: ID!) {
                    enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: MERGE}) {
                        clientMutationId
                    }
                }
                """,
                variables={"pullRequestId": pull_request.node_id},
            )
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code == 400:
                continue
            raise
        else:
            return
    if exception is not None:
        raise exception


async def create_commit_pull_request(
    branch: str,
    new_branch: str,
    message: str,
    body: str,
    project: configuration.GithubProject,
    cwd: Path,
    enable_pre_commit: bool = True,
    skip_pre_commit_hooks: list[str] | None = None,
) -> tuple[bool, githubkit.versions.latest.models.PullRequest | None]:
    """Do a commit, then create a pull request."""
    skip_pre_commit_hooks = skip_pre_commit_hooks or []
    if enable_pre_commit and (cwd / ".pre-commit-config.yaml").exists():
        await run_timeout(
            ["pre-commit", "run", "--all-files", "--show-diff-on-failure"],
            {
                **os.environ,
                "SKIP": ",".join(skip_pre_commit_hooks),
            },
            600,
            "Run pre-commit",
            "Error running pre-commit",
            "Timeout running pre-commit",
            cwd,
        )

    # Print the changes to be committed
    await run_timeout(
        ["git", "diff"],
        None,
        30,
        "Changes to be committed",
        "Error printing changes to be committed",
        "Timeout printing changes to be committed",
        cwd,
    )

    if not await create_commit(message, cwd):
        _LOGGER.debug("No changes to commit")
        return False, None
    return await create_pull_request(branch, new_branch, message, body, project, cwd)


async def close_pull_request_issues(
    new_branch: str,
    message: str,
    github_project: configuration.GithubProject,
) -> None:
    """
    Close the pull request, issue and delete the branch.

    The 'Pull request is open for 5 days' issue.
    """
    pull_requests = (
        await github_project.aio_github.rest.pulls.async_list(
            owner=github_project.owner,
            repo=github_project.repository,
            state="open",
            head=f"{github_project.owner}:{new_branch}",
        )
    ).parsed_data
    for pull_request in pull_requests:  # type: ignore[attr-defined]
        await github_project.aio_github.rest.pulls.async_update(
            owner=github_project.owner,
            repo=github_project.repository,
            pull_number=pull_request.number,
            state="closed",
        )

    if pull_requests:
        await github_project.aio_github.rest.git.async_delete_ref(
            owner=github_project.owner,
            repo=github_project.repository,
            ref=new_branch,
        )

    title = f"Pull request {message} is open for 5 days"
    issues = (
        await github_project.aio_github.rest.issues.async_list_for_repo(
            owner=github_project.owner,
            repo=github_project.repository,
            state="open",
            creator=f"{github_project.application.slug}[bot]",
        )
    ).parsed_data
    assert issues is not None
    for issue in issues:
        if title == issue.title:
            await github_project.aio_github.rest.issues.async_update(
                owner=github_project.owner,
                repo=github_project.repository,
                issue_number=issue.number,
                state="closed",
            )


_SSH_LOCK = asyncio.Lock()


async def git_clone(
    github_project: configuration.GithubProject,
    branch: str | None,
    cwd: Path,
) -> Path | None:
    """Clone the Git repository."""
    # Store the ssh key
    async with _SSH_LOCK:
        directory = await anyio.Path("~/.ssh").expanduser()
        await directory.mkdir(parents=True, exist_ok=True)
        async with await (directory / "id_rsa").open("w", encoding="utf-8") as file:
            await file.write(github_project.application.private_key)

    _, success, _ = await run_timeout(
        [
            "git",
            "clone",
            *(["--depth=1", f"--branch={branch}"] if branch else []),
            f"https://x-access-token:{github_project.token}@github.com/{github_project.owner}/{github_project.repository}.git",
        ],
        None,
        600,
        "Clone repository",
        "Error cloning the repository",
        "Timeout cloning the repository",
        cwd,
    )
    if not success:
        return None

    cwd = cwd / github_project.repository.split("/")[-1]
    user = (
        await github_project.aio_github.rest.users.async_get_by_username(
            github_project.application.slug + "[bot]",
        )
    ).parsed_data
    _, success, _ = await run_timeout(
        [
            "git",
            "config",
            "user.email",
            f"{user.id}+{user.login}@users.noreply.github.com",
        ],
        None,
        60,
        "Set email",
        "Error setting the email",
        "Timeout setting the email",
        cwd,
    )
    if not success:
        return None

    _, success, _ = await run_timeout(
        ["git", "config", "user.name", user.login],
        None,
        60,
        "Set name",
        "Error setting the name",
        "Timeout setting the name",
        cwd,
    )
    if not success:
        return None

    _, success, _ = await run_timeout(
        ["git", "config", "gpg.format", "ssh"],
        None,
        60,
        "Set gpg format",
        "Error setting the gpg format",
        "Timeout setting the gpg format",
        cwd,
    )
    if not success:
        return None

    return cwd


def get_alternate_versions(security: security_md.Security, branch: str) -> list[str]:
    """Get the stabilization versions."""
    alternate_index = security.alternate_tag_index
    version_index = security.version_index

    if version_index < 0:
        _LOGGER.warning("No Version column in the SECURITY.md")
        return []

    last = False
    result = []
    for row in security.data:
        if row[version_index] == branch:
            if alternate_index >= 0:
                result = [v.strip() for v in row[alternate_index].split(",") if v.strip()]
            last = True
        elif last:
            last = False
            break

    if last:
        result.append("latest")

    if not result:
        _LOGGER.warning("Branch %s not found in the SECURITY.md", branch)

    return result


def manage_updated(status: dict[str, Any], key: str, days_old: int = 2) -> None:
    """
    Manage the updated status.

    Add an updated field to the status and remove the old status.
    """
    status.setdefault(key, {})["updated"] = datetime.datetime.now(
        datetime.UTC,
    ).isoformat()
    for other_key, other_object in list(status.items()):
        if (
            not isinstance(other_object, dict)
            or "updated" not in other_object
            or utils.datetime_with_timezone(
                datetime.datetime.fromisoformat(other_object["updated"]),
            )
            < datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_old)
        ):
            _LOGGER.debug(
                "Remove old status %s (%s < %s)",
                other_key,
                other_object.get("updated", "-"),
                datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_old),
            )
            del status[other_key]


def manage_updated_separated(
    updated: dict[str, datetime.datetime],
    data: dict[str, Any],
    key: str,
    days_old: int = 2,
) -> None:
    """
    Manage the updated status.

    Add an updated field to the status and remove the old status.
    """
    updated[key] = datetime.datetime.now(datetime.UTC)
    _LOGGER.debug("Set updated %s to %s", key, updated[key])
    min_date = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days_old)
    for other_key, date in list(updated.items()):
        date = utils.datetime_with_timezone(date)  # noqa: PLW2901
        if date < min_date:
            _LOGGER.debug(
                "Remove old date %s (%s < %s)",
                other_key,
                date,
                min_date,
            )
            del updated[other_key]

    for other_key in list(data.keys()):
        if other_key not in updated:
            _LOGGER.debug("Remove old status %s", other_key)
            del data[other_key]


async def create_checks(
    job: models.Queue,
    session: sqlalchemy.ext.asyncio.AsyncSession,
    current_module: module.Module[Any, Any, Any, Any],
    github_project: configuration.GithubProject,
    service_url: str,
) -> githubkit.versions.latest.models.CheckRun:
    """Create the GitHub check run."""
    # Get the job id from the database
    await session.flush()

    service_url = service_url if service_url.endswith("/") else service_url + "/"
    service_url = urllib.parse.urljoin(service_url, "logs/")
    service_url = urllib.parse.urljoin(service_url, str(job.id))

    sha = None
    if job.github_event_name == "pull_request":
        event_data_pull_request = githubkit.webhooks.parse_obj(  # type: ignore[attr-defined]
            "pull_request",
            job.github_event_data,
        )
        sha = event_data_pull_request.pull_request.head.sha
    if job.github_event_name == "push":
        event_data_push = githubkit.webhooks.parse_obj("push", job.github_event_data)  # type: ignore[attr-defined]
        sha = event_data_push.before if event_data_push.deleted else event_data_push.after
    if job.github_event_name == "workflow_run":
        event_data_workflow_run = githubkit.webhooks.parse_obj(  # type: ignore[attr-defined]
            "workflow_run",
            job.github_event_data,
        )
        sha = event_data_workflow_run.workflow_run.head_sha
    if job.github_event_name == "check_suite":
        event_data_check_suite = githubkit.webhooks.parse_obj(  # type: ignore[attr-defined]
            "check_suite",
            job.github_event_data,
        )
        sha = event_data_check_suite.check_suite.head_sha
    if job.github_event_name == "check_run":
        event_data_check_run = githubkit.webhooks.parse_obj(  # type: ignore[attr-defined]
            "check_run",
            job.github_event_data,
        )
        sha = event_data_check_run.check_run.head_sha
    if sha is None:
        branch = (
            await github_project.aio_github.rest.repos.async_get_branch(
                owner=github_project.owner,
                repo=github_project.repository,
                branch=await github_project.default_branch(),
            )
        ).parsed_data
        sha = branch.commit.sha
    if sha is None:
        message = f"No sha found for the job {job.id} in {job.github_event_name}"
        raise ValueError(message)

    name = f"{current_module.title()}: {job.github_event_name}"
    check_run = (
        await github_project.aio_github.rest.checks.async_create(
            owner=github_project.owner,
            repo=github_project.repository,
            name=name,
            head_sha=sha,
            details_url=service_url,
            external_id=str(job.id),
        )
    ).parsed_data
    job.check_run_id = check_run.id
    await session.commit()
    await session.refresh(job)
    return check_run
