"""the audit modules."""

import asyncio
import base64
import datetime
import json
import logging
import os
import shutil
import subprocess  # nosec
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, cast

import aiofiles
import githubkit.exception
import githubkit.webhooks
import security_md
import yaml
from multi_repo_automation import editor
from pydantic import BaseModel

from github_app_geo_project import models, module
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration
from github_app_geo_project.module.audit import utils as audit_utils

_LOGGER = logging.getLogger(__name__)

_OUTDATED = "Outdated version"


class _TransversalStatusTool(BaseModel):
    title: str


class _TransversalStatusRepo(BaseModel):
    types: dict[str, _TransversalStatusTool] = {}


class _TransversalStatus(BaseModel):
    """The transversal status."""

    updated: dict[str, datetime.datetime] = {}
    """Repository updated time"""
    repositories: dict[str, _TransversalStatusRepo] = {}


class _IntermediateStatus(BaseModel):
    """The intermediate status."""

    status: _TransversalStatusRepo


class _EventData(BaseModel):
    """The event data."""

    type: str | None = None
    snyk: bool = False
    dpkg: bool = False
    is_dashboard: bool = False
    version: str | None = None
    known_versions: list[str] | None = None  # for cleanup


def _get_process_output(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
    short_message: list[str],
    success: bool,
    intermediate_status: _IntermediateStatus,
) -> module.ProcessOutput[_EventData, _IntermediateStatus]:
    assert context.module_event_data.type is not None
    issue_check.set_check(context.module_event_data.type, checked=False)

    return module.ProcessOutput(
        dashboard=issue_check.to_string(),
        intermediate_status=intermediate_status,
        updated_transversal_status=True,
        success=success,
        output={"summary": "\n".join(short_message)} if short_message else {},
    )


async def _process_error(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    key: str,
    issue_check: module_utils.DashboardIssue,
    error_message: list[str | models.OutputData] | None = None,
    message: str | None = None,
) -> str | None:
    output_url = None
    if error_message:
        logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
        output_id = await module_utils.add_output(
            context,
            key,
            [*error_message, f'<a href="{logs_url}">Logs</a>'],
            models.OutputStatus.ERROR,
        )

        output_url = urllib.parse.urljoin(context.service_url, f"output/{output_id}")
        issue_check.set_title(
            key,
            (f"{key}: {message} ([Error]({output_url}))" if message else f"{key} ([Error]({output_url}))"),
        )
    elif message:
        issue_check.set_title(key, f"{key}: {message}")
    else:
        issue_check.set_title(key, f"{key}: everything is fine")

    return output_url


async def _process_renovate(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    known_versions: list[str] | None,
) -> bool:
    # Checkout the right branch on a temporary directory
    with tempfile.TemporaryDirectory() as tmpdirname:
        _LOGGER.debug(
            "Clone the repository in the temporary directory: %s",
            tmpdirname,
        )
        cwd = Path(tmpdirname)
        if context.module_event_data.version is None:
            _LOGGER.debug("Process renovate update on default branch")

            assert known_versions is not None

            default_branch = await context.github_project.default_branch()

            new_cwd = await module_utils.git_clone(context.github_project, default_branch, cwd)
            if new_cwd is None:
                _LOGGER.error("Failed to clone the repository for Renovate update on default branch")
                return False

            with editor.EditRenovateConfigV2(new_cwd / ".github" / "renovate.json5") as renovate_config:
                renovate_config["baseBranchPatterns"] = [
                    default_branch,
                    *known_versions,
                ]

            success, _ = await _create_pull_request_if_changes(
                default_branch,
                f"ghci/audit/renovate/{default_branch}",
                "Update Renovate configuration",
                "Update the stabilization branches in the Renovate configuration",
                context,
                {},
                new_cwd,
                None,
            )
            return success
        _LOGGER.debug(
            "Process Renovate cleanup for version %s",
            context.module_event_data.version,
        )
        new_cwd = await module_utils.git_clone(context.github_project, context.module_event_data.version, cwd)
        if new_cwd is None:
            _LOGGER.error(
                "Failed to clone the repository for Renovate cleanup on version %s",
                context.module_event_data.version,
            )
            return False

        renovate_config_path = new_cwd / ".github" / "renovate.json5"
        if renovate_config_path.exists():
            renovate_config_path.unlink()
        security_md_path = new_cwd / "SECURITY.md"
        if security_md_path.exists():
            security_md_path.unlink()

        success, _ = await _create_pull_request_if_changes(
            context.module_event_data.version,
            f"ghci/audit/renovate/{context.module_event_data.version}",
            f"Cleanup Renovate configuration for version {context.module_event_data.version}",
            "Remove the Renovate configuration and the SECURITY.md file if they exist",
            context,
            {},
            new_cwd,
            None,
        )
        return success


async def _process_outdated(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
) -> None:
    try:
        security_file = (
            await context.github_project.aio_github.rest.repos.async_get_content(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                path="SECURITY.md",
            )
        ).parsed_data
        assert isinstance(security_file, githubkit.versions.latest.models.ContentFile)
        assert security_file.content is not None
        security = security_md.Security(
            base64.b64decode(security_file.content).decode("utf-8"),
        )

        error_message = audit_utils.outdated_versions(security)
        await _process_error(context, _OUTDATED, issue_check, error_message)
    except githubkit.exception.RequestFailed as exception:
        if exception.response.status_code == 404:
            _LOGGER.debug("No SECURITY.md file in the repository")
            await _process_error(
                context,
                _OUTDATED,
                issue_check,
                message="No SECURITY.md file in the repository",
            )
        else:
            _LOGGER.exception("Error while getting SECURITY.md")
            await _process_error(
                context,
                _OUTDATED,
                issue_check,
                message="Error while getting SECURITY.md",
            )
            raise


async def _process_snyk_dpkg(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
    intermediate_status: _IntermediateStatus,
) -> tuple[list[str], bool]:
    short_message: list[str] = []
    success = True

    key = f"Undefined {context.module_event_data.version}"
    new_branch = f"ghci/audit/{context.module_event_data.type}/{context.module_event_data.version}"
    if context.module_event_data.type == "snyk":
        key = f"Snyk check/fix {context.module_event_data.version}"
    if context.module_event_data.type == "dpkg":
        key = f"Dpkg {context.module_event_data.version}"
    try:
        branch: str = cast("str", context.module_event_data.version)

        # Checkout the right branch on a temporary directory
        with tempfile.TemporaryDirectory() as tmpdirname:
            _LOGGER.debug(
                "Clone the repository in the temporary directory: %s",
                tmpdirname,
            )
            cwd = Path(tmpdirname)
            new_cwd = await module_utils.git_clone(context.github_project, branch, cwd)
            if new_cwd is None:
                return ["Fail to clone the repository"], False
            cwd = new_cwd

            local_config: configuration.AuditConfiguration = {}

            ghci_config_path = cwd / ".github" / "ghci.yaml"
            if context.module_event_data.type in ("snyk", "dpkg") and ghci_config_path.exists():
                async with aiofiles.open(ghci_config_path, encoding="utf-8") as file:
                    local_config = yaml.load(
                        await file.read(),
                        Loader=yaml.SafeLoader,
                    ).get("audit", {})

            logs_url = urllib.parse.urljoin(
                context.service_url,
                f"logs/{context.job_id}",
            )
            if context.module_event_data.type == "snyk":
                python_version = ""
                tool_versions = cwd / ".tool-versions"
                if tool_versions.exists():
                    async with aiofiles.open(tool_versions, encoding="utf-8") as file:
                        async for line in file:
                            if line.startswith("python "):
                                python_version = ".".join(
                                    line.split(" ")[1].split(".")[0:2],
                                ).strip()
                                break

                env = await _use_python_version(python_version, cwd) if python_version else os.environ.copy()

                result, body, short_message, new_success = await audit_utils.snyk(
                    branch,
                    context.module_config,
                    local_config,
                    context.module_config.get("snyk", {}),
                    local_config.get("snyk", {}),
                    logs_url,
                    env,
                    cwd,
                )
                body_md = body.to_markdown() if body is not None else ""
                del body
                success &= new_success
                output_url = await _process_error(
                    context,
                    key,
                    issue_check,
                    [{"title": m.title, "children": [m.to_html("no-title")]} for m in result],
                    ", ".join(short_message),
                )
                message: module_utils.Message = module_utils.HtmlMessage(
                    f"<a href='{output_url}'>Output</a>",
                )
                message.title = "Output URL"
                _LOGGER.debug(message)
                if output_url is not None:
                    short_message.append(f"[Output]({output_url})")
                    if body_md:
                        body_md += "\n\n"
                    body_md += f"[Output]({output_url})" if output_url is not None else ""

            if context.module_event_data.type == "dpkg":
                body_md = "Update dpkg packages"

                if (cwd / "ci" / "dpkg-versions.yaml").exists() or (
                    cwd / ".github" / "dpkg-versions.yaml"
                ).exists():
                    await audit_utils.dpkg(
                        context.module_config.get("dpkg", {}),
                        local_config.get("dpkg", {}),
                        cwd,
                    )

            body_md += "\n" if body_md else ""
            body_md += f"[Logs]({logs_url})"
            short_message.append(f"[Logs]({logs_url})")

            new_success, pr_messages = await _create_pull_request_if_changes(
                branch,
                new_branch,
                key,
                body_md,
                context,
                local_config,
                cwd,
                issue_check,
            )
            success &= new_success
            short_message.extend(pr_messages)

        transversal_message = ", ".join(short_message)
        intermediate_status.status.types[key] = _TransversalStatusTool(
            title=f"{key}: {transversal_message}",
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as proc_error:
        message = module_utils.AnsiProcessMessage(
            cast("list[str]", proc_error.args),
            (None if isinstance(proc_error, subprocess.TimeoutExpired) else proc_error.returncode),
            proc_error.output,
            cast("str", proc_error.stderr),
        )
        _LOGGER.exception("Audit %s process error", key)
        return [f"Error while processing the audit {key}: {proc_error}"], False
    except Exception as exception:  # pylint: disable=broad-except
        _LOGGER.exception("Audit %s error", key)
        return [f"Error while processing the audit {key}: {exception}"], False

    return short_message, success


async def _use_python_version(python_version: str, cwd: Path) -> dict[str, str]:
    command = ["pyenv", "local", python_version]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(300):
        stdout, stderr = await proc.communicate()
    message = module_utils.AnsiProcessMessage.from_async_artifacts(
        command,
        proc,
        stdout,
        stderr,
    )
    if proc.returncode != 0:
        message.title = f"Error while setting the Python version to {python_version}"
        _LOGGER.error(message)
    else:
        message.title = f"Setting the Python version to {python_version}"
        _LOGGER.debug(message)
    command = ["python", "--version"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(5):
        stdout, stderr = await proc.communicate()

    # Get path from /pyenv/versions/{python_version}.*/bin/
    env = os.environ.copy()
    bin_paths = list(Path("/pyenv/versions/").glob(f"{python_version}.*/bin"))
    if bin_paths:
        env["PATH"] = f"{bin_paths[0]}:{env['PATH']}"

    message = module_utils.AnsiProcessMessage.from_async_artifacts(
        command,
        proc,
        stdout,
        stderr,
    )
    message.title = "Python version"
    _LOGGER.debug(message)

    # Cleanup the packages
    shutil.rmtree(f"/var/www/.local/lib/python{python_version}", ignore_errors=True)

    return env


async def _create_pull_request_if_changes(
    branch: str,
    new_branch: str,
    key: str,
    body_md: str,
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    local_config: configuration.AuditConfiguration,
    cwd: Path,
    issue_check: module_utils.DashboardIssue | None,
) -> tuple[bool, list[str]]:
    """Create a pull request if there are changes to commit."""
    success = True
    short_message: list[str] = []

    command = ["git", "diff", "--quiet"]
    diff_proc = await asyncio.create_subprocess_exec(*command, cwd=cwd)
    try:
        async with asyncio.timeout(60):
            await diff_proc.communicate()
        if diff_proc.returncode != 0:
            command = ["git", "diff"]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                message = module_utils.AnsiProcessMessage.from_async_artifacts(
                    command,
                    proc,
                    stdout,
                    stderr,
                )
                message.title = "Changes to be committed"
                _LOGGER.debug(message)
            except TimeoutError:
                proc.kill()
                raise

            command = ["git", "checkout", "-b", new_branch]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    message = module_utils.AnsiProcessMessage.from_async_artifacts(
                        command,
                        proc,
                        stdout,
                        stderr,
                    )
                    message.title = "Error while creating the new branch"
                    _LOGGER.error(message)

                else:
                    pre_commit_config = audit_utils.get_pre_commit_config(
                        context.module_config,
                        local_config,
                    )
                    new_success, pull_request = await module_utils.create_commit_pull_request(
                        branch,
                        new_branch,
                        f"Audit {key}",
                        body_md,
                        context.github_project,
                        cwd,
                        pre_commit_config.get("enabled", True),
                        pre_commit_config.get("skip-hooks", []),
                    )
                    success &= new_success
                    if not new_success:
                        _LOGGER.error(
                            "Error while create commit or pull request",
                        )
                    elif pull_request is not None and issue_check is not None:
                        issue_check.set_title(
                            key,
                            f"{key} ([Pull request]({pull_request.html_url}))",
                        )
                        short_message.append(
                            f"[Pull request]({pull_request.html_url})",
                        )
            except TimeoutError:
                proc.kill()
                raise

        else:
            _LOGGER.debug("No changes to commit")
            await module_utils.close_pull_request_issues(
                new_branch,
                f"Audit {key}",
                context.github_project,
            )
    except TimeoutError:
        try:
            diff_proc.kill()
        except:  # noqa: S110
            pass
        raise

    return success, short_message


class Audit(
    module.Module[
        configuration.AuditConfiguration,
        _EventData,
        _TransversalStatus,
        _IntermediateStatus,
    ],
):
    """The audit module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Audit (Snyk/dpkg)"

    def description(self) -> str:
        """Get the description of the module."""
        return "Audit the project with Snyk (for CVE in dependency) and update dpkg package version to trigger a rebuild, also update the Renovate configuration"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Audit"

    def required_issue_dashboard(self) -> bool:
        """Check if the module requires an issue dashboard."""
        return True

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.module_event_name == "push":
            event_data_push = githubkit.webhooks.parse_obj(
                "push",
                context.github_event_data,
            )
            for commit in event_data_push.commits:
                # Check if SECURITY.md is removed on the default branch
                if (
                    "SECURITY.md" in (commit.removed or [])
                    and event_data_push.ref == f"refs/heads/{event_data_push.repository.default_branch}"
                ):
                    return [
                        module.Action(
                            priority=module.PRIORITY_CRON,
                            data=_EventData(type="cleanup"),
                            title="cleanup",
                        ),
                    ]

                if "SECURITY.md" in [
                    *(commit.modified or []),
                    *(commit.added or []),
                ]:
                    return [
                        module.Action(
                            priority=module.PRIORITY_CRON,
                            data=_EventData(type="outdated"),
                            title="outdated",
                        ),
                    ]
        results: list[module.Action[_EventData]] = []
        snyk = False
        dpkg = False
        is_dashboard = context.module_event_name == "dashboard"
        if is_dashboard:
            old_check = module_utils.DashboardIssue(
                context.github_event_data.get("old_data", "").split("<!---->")[0],
            )
            new_check = module_utils.DashboardIssue(
                context.github_event_data.get("new_data", "").split("<!---->")[0],
            )

            if not old_check.is_checked("outdated") and new_check.is_checked(
                "outdated",
            ):
                results.append(
                    module.Action(
                        priority=module.PRIORITY_STANDARD,
                        data=_EventData(type="outdated"),
                        title="outdated",
                    ),
                )
            if not old_check.is_checked("snyk") and new_check.is_checked("snyk"):
                snyk = True
            if not old_check.is_checked("dpkg") and new_check.is_checked("dpkg"):
                dpkg = True

        if (
            context.github_event_data.get("type") == "event"
            and context.github_event_data.get("name") == "daily"
        ):
            results.append(
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data=_EventData(type="outdated"),
                    title="outdated",
                )
            )
            results.append(
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data=_EventData(type="renovate"),
                    title="renovate",
                )
            )
            snyk = True
            dpkg = True

        if dpkg or snyk:
            results.append(
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data=_EventData(snyk=snyk, dpkg=dpkg, is_dashboard=is_dashboard),
                ),
            )
        return results

    async def process(
        self,
        context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    ) -> module.ProcessOutput[_EventData, _IntermediateStatus]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        issue_check = module_utils.DashboardIssue(context.issue_data)
        short_message: list[str] = []
        success = True
        intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())

        # Handle cleanup when SECURITY.md is removed on default branch
        if context.module_event_data.type == "cleanup":
            _LOGGER.info("Cleaning up audit-related pull requests and issues")
            known_versions = context.module_event_data.known_versions or []
            # Close all audit-related pull requests
            async for branch in context.github_project.aio_github.paginate(
                context.github_project.aio_github.rest.repos.async_list_branches,
                owner=context.github_project.owner,
                repo=context.github_project.repository,
            ):
                branch_name = branch.name
                for key_prefix in ["snyk", "dpkg", "renovate"]:
                    if branch_name.startswith(f"ghci/audit/{key_prefix}/"):
                        _LOGGER.debug("Closing pull requests for branch %s", branch_name)

                        version = branch_name.split("/", 3)[-1]
                        if version not in known_versions:
                            issue_message_middle = (
                                "Snyk check/fix"
                                if key_prefix == "snyk"
                                else "Dpkg"
                                if key_prefix == "dpkg"
                                else "Renovate"
                            )
                            await module_utils.close_pull_request_issues(
                                branch_name,
                                f"Audit {issue_message_middle} {version}",
                                context.github_project,
                            )

            issue: githubkit.versions.latest.models.Issue
            async for issue in context.github_project.aio_github.paginate(
                context.github_project.aio_github.rest.issues.async_list_for_repo,
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                state="open",
                creator=f"{context.github_project.application.slug}[bot]",
            ):
                issue_title: str = issue.title
                for key_prefix in ["Snyk check/fix", "Dpkg", "Renovate"]:
                    prefix = f"Pull request Audit {key_prefix} "
                    if issue_title.startswith(prefix):
                        version = issue_title[len(prefix) :].split(" ", 1)[0]
                        if version not in known_versions:
                            _LOGGER.debug("Closing issue %s", issue.html_url)
                            await context.github_project.aio_github.rest.issues.async_update(
                                owner=context.github_project.owner,
                                repo=context.github_project.repository,
                                issue_number=issue.number,
                                state="closed",
                            )

            if not known_versions:
                # Clear all checks from dashboard
                issue_check.remove_check("outdated")
                issue_check.remove_check("snyk")
                issue_check.remove_check("dpkg")

                return module.ProcessOutput(
                    dashboard=issue_check.to_string(),
                    success=True,
                )
            return module.ProcessOutput(success=True)

        # If no SECURITY.md apply on default branch
        key_starts = []
        security_file = None
        try:
            security_file = (
                await context.github_project.aio_github.rest.repos.async_get_content(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    path="SECURITY.md",
                )
            ).parsed_data
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code == 404:
                _LOGGER.debug("No security file in the repository")
            else:
                raise
        if security_file is not None:
            key_starts.append(_OUTDATED)
            issue_check.add_check("outdated", "Check outdated version", checked=False)
        else:
            issue_check.remove_check("outdated")

        if security_file is not None and context.module_config.get("snyk", {}).get(
            "enabled",
            configuration.ENABLE_SNYK_DEFAULT,
        ):
            issue_check.add_check(
                "snyk",
                "Check security vulnerabilities with Snyk",
                checked=False,
            )
            key_starts.append("Snyk check/fix ")
        else:
            issue_check.remove_check("snyk")

        dpkg_version = None
        try:
            dpkg_version = (
                await context.github_project.aio_github.rest.repos.async_get_content(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    path=".github/dpkg-versions.yaml",
                )
            ).parsed_data
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code == 404:
                _LOGGER.debug("No dpkg-versions.yaml file in the repository")
            else:
                raise
        if (
            security_file is not None
            and context.module_config.get("dpkg", {}).get(
                "enabled",
                configuration.ENABLE_DPKG_DEFAULT,
            )
            and dpkg_version is not None
        ):
            issue_check.add_check("dpkg", "Update dpkg packages", checked=False)
            key_starts.append("Dpkg ")
        else:
            issue_check.remove_check("dpkg")

        if context.module_event_data.type == "renovate" and context.module_config.get("renovate", {}).get(
            "enabled",
            configuration.ENABLE_RENOVATE_DEFAULT,
        ):
            actions = []
            mapped_versions = None
            if context.module_event_data.version is None:
                # Creates new jobs with the versions from the SECURITY.md
                versions = []
                if (
                    isinstance(security_file, githubkit.versions.latest.models.ContentFile)
                    and security_file.content is not None
                ):
                    security = security_md.Security(
                        base64.b64decode(security_file.content).decode("utf-8"),
                    )

                    versions = security.branches()
                else:
                    _LOGGER.debug(
                        "No SECURITY.md file in the repository, nothing to do for Renovate",
                    )
                    return module.ProcessOutput()

                mapped_versions = [
                    context.module_config.get("version-mapping", {}).get(version, version)
                    for version in versions
                ]

                _LOGGER.debug("Versions: %s", ", ".join(versions))
                actions.extend(
                    [
                        module.Action(
                            priority=module.PRIORITY_CRON,
                            data=_EventData(type="renovate", version=version, known_versions=mapped_versions),
                            title=f"renovate ({version})",
                        )
                        for version in mapped_versions
                    ],
                )

            success = await _process_renovate(context, mapped_versions)
            return module.ProcessOutput(actions=actions, success=success)
        if context.module_event_data.type == "outdated":
            await _process_outdated(context, issue_check)
        elif context.module_event_data.version is None:
            # Creates new jobs with the versions from the SECURITY.md
            versions = []
            if (
                isinstance(security_file, githubkit.versions.latest.models.ContentFile)
                and security_file.content is not None
            ):
                security = security_md.Security(
                    base64.b64decode(security_file.content).decode("utf-8"),
                )

                versions = security.branches()
            else:
                _LOGGER.debug(
                    "No SECURITY.md file in the repository, nothing to audit",
                )
                return module.ProcessOutput(
                    actions=[
                        module.Action(
                            priority=module.PRIORITY_CRON,
                            data=_EventData(type="cleanup"),
                            title="cleanup",
                        )
                    ],
                    dashboard=issue_check.to_string(),
                )
            _LOGGER.debug("Versions: %s", ", ".join(versions))

            all_key_starts = []
            for key in key_starts:
                if key == _OUTDATED:
                    all_key_starts.append(_OUTDATED)
                else:
                    all_key_starts.extend([f"{key}{version}" for version in versions])

            for key in list(intermediate_status.status.types.keys()):
                if key not in all_key_starts:
                    intermediate_status.status.types.pop(key)

            # Audit is relay slow than add 15 to the cron priority
            priority = (
                module.PRIORITY_STANDARD if context.module_event_data.is_dashboard else module.PRIORITY_CRON
            )
            # Apply version mapping to get the actual branch names used
            mapped_versions = [
                context.module_config.get("version-mapping", {}).get(version, version) for version in versions
            ]
            actions = [
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data=_EventData(type="cleanup", known_versions=mapped_versions),
                    title="cleanup",
                )
            ]
            for version in mapped_versions:
                if context.module_event_data.snyk and context.module_config.get(
                    "snyk",
                    {},
                ).get(
                    "enabled",
                    configuration.ENABLE_SNYK_DEFAULT,
                ):
                    actions.append(
                        module.Action(
                            priority=priority,
                            data=_EventData(type="snyk", version=version),
                            title=f"snyk ({version})",
                        ),
                    )
                if context.module_event_data.dpkg and context.module_config.get(
                    "dpkg",
                    {},
                ).get(
                    "enabled",
                    configuration.ENABLE_DPKG_DEFAULT,
                ):
                    actions.append(
                        module.Action(
                            priority=priority,
                            data=_EventData(type="dpkg", version=version),
                            title=f"dpkg ({version})",
                        ),
                    )
            return ProcessOutput(
                actions=actions,
                intermediate_status=intermediate_status,
                updated_transversal_status=True,
            )
        else:
            short_message, success = await _process_snyk_dpkg(
                context,
                issue_check,
                intermediate_status,
            )

        return _get_process_output(
            context,
            issue_check,
            short_message,
            success,
            intermediate_status,
        )

    async def update_transversal_status(
        self,
        context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
        intermediate_status: _IntermediateStatus,
        transversal_status: _TransversalStatus,
    ) -> _TransversalStatus:
        """Update the transversal status with the intermediate status."""
        key = f"{context.github_project.owner}/{context.github_project.repository}"
        module_utils.manage_updated_separated(
            transversal_status.updated,
            transversal_status.repositories,
            key,
        )
        transversal_status.repositories[key] = intermediate_status.status
        return transversal_status

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        with (Path(__file__).parent / "schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("audit")  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
                "issues": "write",
                "contents": "write",
                "workflows": "write",
            },
            {"push"},
        )

    def has_transversal_dashboard(self) -> bool:
        """Say that the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self,
        context: module.TransversalDashboardContext[_TransversalStatus],
    ) -> module.TransversalDashboardOutput:
        """Get the transversal dashboard content."""
        repositories = []
        for repository, data in context.status.repositories.items():
            if data:
                repositories.append(
                    {
                        "repository": repository,
                        "data": data,
                    },
                )
        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/audit/dashboard.html",
            data={"repositories": repositories},
        )
