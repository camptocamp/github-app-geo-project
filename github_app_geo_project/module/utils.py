"""Module utility functions for the modules."""

import asyncio
import datetime
import logging
import os
import re
import shlex
import subprocess  # nosec
from typing import Any, cast

import github
import html_sanitizer
import markdownify
import security_md
from ansi2html import Ansi2HTMLConverter

from github_app_geo_project import configuration, models, module

_LOGGER = logging.getLogger(__name__)
WORKING_DIRECTORY_LOCK = asyncio.Lock()


def add_output(
    context: module.ProcessContext[Any, Any, Any],
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


DashboardIssueRaw = list[DashboardIssueItem | str]

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

_BOLD_RE = re.compile(r'<span style="font-weight: bold(;[^"]*)?">([^<]*)</span>')
_ITALIC_RE = re.compile(r'<span style="font-style: italic(;[^"]*)?">([^<]*)</span>')


def html_to_markdown(html: str) -> str:
    """Convert HTML to markdown."""
    html = _BOLD_RE.sub(r"<b>\2</b>", html)
    html = _ITALIC_RE.sub(r"<i>\2</i>", html)
    return cast(str, markdownify.markdownify(html))


class HtmlMessage(Message):
    """Utility class to convert HTML messages to HTML/markdown."""

    def __init__(self, html: str, title: str = "") -> None:
        """Initialize the ANSI message."""
        self.html = html
        self.title = title

    def to_html(self, style: str = "h3") -> str:
        """Convert the ANSI message to HTML."""
        global _suffix  # pylint: disable=global-statement

        # interpret template parameters
        html = self.html.replace("{pre}", "<pre>" if style != "collapse" else "").replace(
            "{post}", "</pre>" if style != "collapse" else ""
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
                    ]
                )
            else:
                html = "\n".join([f"<{style}>{self.title}</{style}>", html])

        sanitizer = html_sanitizer.Sanitizer(
            {
                "tags": {*html_sanitizer.sanitizer.DEFAULT_SETTINGS["tags"], "span", "div", "pre", "code"},
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
            }
        )
        return cast(str, sanitizer.sanitize(html))

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
                ]
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
            return f"<details><summary>{self.title}</summary>{html_to_markdown(self.raw_html)}</details>"
        return html_to_markdown(self.raw_html)

    def to_plain_text(self) -> str:
        """Get the process message."""
        return self.to_markdown()


class AnsiProcessMessage(AnsiMessage):
    """Represent a message from a subprocess."""

    def __init__(
        self, args: list[str], returncode: int | None, stdout: str, stderr: str, error: str | None = None
    ) -> None:
        """Initialize the process message."""
        self.args: list[str] = []

        for arg in args:
            if "x-access-token" in str(arg):
                self.args.append(re.sub(r"x-access-token:[0-9a-zA-Z_]*", "x-access-token:***", arg))
            else:
                self.args.append(arg)

        self.returncode = returncode
        self.stdout = self._ansi_converter.convert(stdout or "", full=False)
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
            [
                "<details>",
                f"<summary>{self.title}</summary>",
                f"Command: {shlex.join(self.args)}",
                f"Return code: {self.returncode}",
                *(
                    ["", "Output:", "```", html_to_markdown(self.stdout.strip()), "```"]
                    if self.stdout.strip()
                    else []
                ),
                *(
                    ["", "Error:", "```", html_to_markdown(self.stderr.strip()), "```"]
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
                    ["", "Output:", "```", html_to_markdown(self.stdout.strip()), "```"]
                    if self.stdout.strip()
                    else []
                ),
                *(
                    ["", "Error:", "```", html_to_markdown(self.stderr.strip()), "```"]
                    if self.stderr.strip()
                    else []
                ),
            ]
        )

    @staticmethod
    def from_process(
        proc: subprocess.CompletedProcess[str] | subprocess.CalledProcessError | subprocess.TimeoutExpired,
    ) -> "AnsiProcessMessage":
        """Create a process message from a subprocess."""
        if isinstance(proc, subprocess.TimeoutExpired):
            return AnsiProcessMessage(cast(list[str], proc.args), None, proc.output, cast(str, proc.stderr))
        return AnsiProcessMessage(cast(list[str], proc.args), proc.returncode, proc.stdout, proc.stderr)


def ansi_proc_message(
    proc: subprocess.CompletedProcess[str] | subprocess.CalledProcessError | subprocess.TimeoutExpired,
) -> Message:
    """
    Process the output of a subprocess for the dashboard (markdown)/HTML.

    Arguments:
    ---------
    proc: The subprocess result

    Return:
    ------
    The dashboard message, the simple error message, the style
    """
    return AnsiProcessMessage.from_process(proc)


def get_cwd() -> str | None:
    """
    Get the current working directory.

    Did not raise an exception if it does not exist, return None instead.
    """
    try:
        return os.getcwd()
    except FileNotFoundError:
        return None


async def run_timeout(
    command: list[str],
    env: dict[str, str] | None,
    timeout: int,
    success_message: str,
    error_message: str,
    timeout_message: str,
    cwd: str | None = None,
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
    start = datetime.datetime.now()
    try:
        async with asyncio.timeout(timeout):
            try:
                async_proc = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=cwd or get_cwd(),
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await async_proc.communicate()
            finally:
                _LOGGER.debug("Command %s finished", shlex.join(command))
            assert async_proc.returncode is not None
            message: Message = AnsiProcessMessage(
                command, async_proc.returncode, stdout.decode(), stderr.decode()
            )
            success = async_proc.returncode == 0
            if success:
                message.title = f"{success_message}, in {datetime.datetime.now() - start}s."
                _LOGGER.debug(message)
            else:
                message.title = f"{error_message}, in {datetime.datetime.now() - start}s."
                _LOGGER.warning(message)
            return stdout.decode(), success, message
    except FileNotFoundError as exception:
        if error:
            _LOGGER.exception("%s not found: %s", command[0], exception)
        else:
            _LOGGER.warning("%s not found", command[0])
        proc = subprocess.run(  # pylint: disable=subprocess-run-check
            ["find", "/", "-name", command[0]],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        message = ansi_proc_message(proc)
        message.title = f"Find {command[0]}"
        _LOGGER.debug(message)
        return None, False, message
    except asyncio.TimeoutError as exception:
        if async_proc:
            async_proc.kill()
            message = AnsiProcessMessage(
                command,
                None,
                "" if async_proc.stdout is None else (await async_proc.stdout.read()).decode(),
                "" if async_proc.stderr is None else (await async_proc.stderr.read()).decode(),
                error=str(exception),
            )
            message.title = timeout_message
            _LOGGER.warning(message)
            return None, False, message
        else:
            if error:
                _LOGGER.exception("TimeoutError for %s: %s", command[0], exception)
            else:
                _LOGGER.warning("TimeoutError for %s", command[0])
            return None, False, AnsiProcessMessage(command, None, "", "", str(exception))


def has_changes(include_un_followed: bool = False) -> bool:
    """Check if there are changes."""
    if include_un_followed:
        proc = subprocess.run(  # pylint: disable=subprocess-run-check
            ["git", "status", "--porcelain"], capture_output=True, encoding="utf-8", timeout=30
        )
        return bool(proc.stdout)
    proc = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", "diff", "--exit-code"], capture_output=True, encoding="utf-8", timeout=30
    )
    return proc.returncode != 0


async def create_commit(message: str, pre_commit_check: bool = True) -> bool:
    """Do a commit."""
    proc = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", "add", "--all"], capture_output=True, encoding="utf-8", timeout=30
    )
    if proc.returncode != 0:
        proc_message = ansi_proc_message(proc)
        proc_message.title = "Error adding files to commit"
        _LOGGER.warning(proc_message)
        return False
    _, success, _ = await run_timeout(
        ["git", "commit", f"--message={message}", *([] if pre_commit_check else ["--no-verify"])],
        None,
        600,
        "Commit",
        "Error committing files",
        "Timeout committing files",
    )
    if not success and pre_commit_check:
        # On pre-commit issues, add them to the commit, and try again without the pre-commit
        success = await create_commit(message, False)
    return success


def create_pull_request(
    branch: str, new_branch: str, message: str, body: str, project: configuration.GithubProject
) -> tuple[bool, github.PullRequest.PullRequest | None]:
    """Create a pull request."""
    proc = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", "push", "--force", "origin", new_branch],
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    if proc.returncode != 0:
        proc_message = ansi_proc_message(proc)
        proc_message.title = "Error pushing branch"
        _LOGGER.warning(proc_message)
        return False, None

    pulls = project.repo.get_pulls(state="open", head=f"{project.repo.full_name.split('/')[0]}:{new_branch}")
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
            _LOGGER.warning("Pull request #%s is open for 5 days", pull_request.number)
            title = f"Pull request {message} is open for 5 days"
            body = f"See: #{pull_request.number}"
            found = False
            issues = project.repo.get_issues(
                state="open",
                creator=project.application.integration.get_app().slug + "[bot]",  # type: ignore[arg-type]
            )
            if issues.totalCount > 0:
                for candidate in issues:
                    if title == candidate.title:
                        candidate.create_comment("The pull request is still open.")
                        found = True
                        if body != candidate.body:
                            candidate.edit(body=body)
            if not found:
                project.repo.create_issue(
                    title=title,
                    body=body,
                )
            return False, pull_request
    else:
        pull_request = project.repo.create_pull(
            title=message,
            body=body,
            head=new_branch,
            base=branch,
        )
        pull_request.enable_automerge(merge_method="SQUASH")
        return True, pull_request
    return True, None


async def create_commit_pull_request(
    branch: str, new_branch: str, message: str, body: str, project: configuration.GithubProject
) -> tuple[bool, github.PullRequest.PullRequest | None]:
    """Do a commit, then create a pull request."""
    if os.path.exists(".pre-commit-config.yaml"):
        try:
            proc = subprocess.run(  # pylint: disable=subprocess-run-check
                ["pre-commit", "install"],
                capture_output=True,
                encoding="utf-8",
                timeout=10,
            )
            proc_message = ansi_proc_message(proc)
            proc_message.title = "Install pre-commit"
            _LOGGER.debug(proc_message)
        except FileNotFoundError:
            _LOGGER.debug("pre-commit not installed")
    if not await create_commit(message):
        return False, None
    return create_pull_request(branch, new_branch, message, body, project)


def close_pull_request_issues(new_branch: str, message: str, project: configuration.GithubProject) -> None:
    """
    Close the pull request, issue and delete the branch.

    The 'Pull request is open for 5 days' issue.
    """
    pulls = project.repo.get_pulls(state="open", head=f"{project.repo.full_name.split('/')[0]}:{new_branch}")
    if pulls.totalCount > 0:
        pull_request = pulls[0]
        pull_request.edit(state="closed")

        project.repo.get_git_ref(f"heads/{new_branch}").delete()

    title = f"Pull request {message} is open for 5 days"
    issues = project.repo.get_issues(
        state="open",
        creator=project.application.integration.get_app().slug + "[bot]",  # type: ignore[arg-type]
    )
    for issue in issues:
        if title == issue.title:
            issue.edit(state="closed")


def git_clone(github_project: configuration.GithubProject, branch: str) -> bool:
    """Clone the Git repository."""
    # Store the ssh key
    directory = os.path.expanduser("~/.ssh/")
    if not os.path.exists(directory):
        os.makedirs(directory)
    with open(os.path.join(directory, "id_rsa"), "w", encoding="utf-8") as file:
        file.write(github_project.application.auth.private_key)

    proc = subprocess.run(  # pylint: disable=subprocess-run-check
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
        _LOGGER.warning(message)
        return False
    message.title = "Clone repository"
    _LOGGER.debug(message)

    os.chdir(github_project.repository.split("/")[-1])
    app = github_project.application.integration.get_app()
    user = github_project.github.get_user(app.slug + "[bot]")
    proc = subprocess.run(  # pylint: disable=subprocess-run-check
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
        _LOGGER.warning(message)
        return False
    message.title = "Set email"
    _LOGGER.debug(message)

    proc = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", "config", "user.name", user.login],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error setting the name"
        _LOGGER.warning(message)
        return False
    message.title = "Set name"
    _LOGGER.debug(message)

    proc = subprocess.run(  # pylint: disable=subprocess-run-check
        ["git", "config", "gpg.format", "ssh"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error setting the gpg format"
        _LOGGER.warning(message)
        return False
    message.title = "Set gpg format"
    _LOGGER.debug(message)

    return True


def get_stabilization_versions(security: security_md.Security) -> list[str]:
    """Get the stabilization versions."""
    version_index = security.version_index
    supported_until_index = security.support_until_index
    alternates_tag_index = security.alternate_tag_index

    if version_index < 0:
        _LOGGER.warning("No Version column in the SECURITY.md")
        return []
    if supported_until_index < 0:
        _LOGGER.warning("No Supported Until column in the SECURITY.md")
        return []

    versions = []
    alternate_tags = []
    for row in security.data:
        if row[supported_until_index] != "Unsupported":
            versions.append(row[version_index])
        if alternates_tag_index >= 0:
            alternate_tags.extend([v.strip() for v in row[alternates_tag_index].split(",") if v.strip()])
    return [v for v in versions if v not in alternate_tags]


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


def manage_updated_separated(
    updated: dict[str, datetime.datetime], data: dict[str, Any], key: str, days_old: int = 2
) -> None:
    """
    Manage the updated status.

    Add an updated field to the status and remove the old status.
    """
    updated[key] = datetime.datetime.now()
    _LOGGER.debug("Set updated %s to %s", key, updated[key])
    min_date = datetime.datetime.now() - datetime.timedelta(days=days_old)
    for other_key, date in list(updated.items()):
        if date < min_date:
            _LOGGER.debug(
                "Remove old date %s (%s < %s)",
                other_key,
                date,
                datetime.datetime.now() - datetime.timedelta(days=days_old),
            )
            del updated[other_key]

    for other_key in list(data.keys()):
        if other_key not in updated:
            _LOGGER.debug("Remove old status %s", other_key)
            del data[other_key]
