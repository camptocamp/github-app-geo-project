"""the audit modules."""

import asyncio
import datetime
import glob
import json
import logging
import os
import os.path
import shutil
import subprocess  # nosec
import tempfile
import urllib.parse
from typing import Any, cast

import github
import security_md
import yaml
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


class _EventData(BaseModel):
    """The event data."""

    type: str | None = None
    snyk: bool = False
    dpkg: bool = False
    is_dashboard: bool = False
    version: str | None = None


def _get_process_output(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData, _TransversalStatus],
    issue_check: module_utils.DashboardIssue,
    short_message: list[str],
    success: bool,
) -> module.ProcessOutput[_EventData, _TransversalStatus]:
    assert context.module_event_data.type is not None
    issue_check.set_check(context.module_event_data.type, False)

    return module.ProcessOutput(
        dashboard=issue_check.to_string(),
        transversal_status=context.transversal_status,
        success=success,
        output={"summary": "\n".join(short_message)} if short_message else {},
    )


def _process_error(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData, _TransversalStatus],
    key: str,
    issue_check: module_utils.DashboardIssue,
    error_message: list[str | models.OutputData] | None = None,
    message: str | None = None,
) -> str | None:
    output_url = None
    if error_message:
        logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
        output_id = module_utils.add_output(
            context,
            key,
            [*error_message, f'<a href="{logs_url}">Logs</a>'],
            models.OutputStatus.ERROR,
        )

        output_url = urllib.parse.urljoin(context.service_url, f"output/{output_id}")
        issue_check.set_title(
            key, f"{key}: {message} ([Error]({output_url}))" if message else f"{key} ([Error]({output_url}))"
        )
    elif message:
        issue_check.set_title(key, f"{key}: {message}")
    else:
        issue_check.set_title(key, f"{key}: everything is fine")

    return output_url


def _process_outdated(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData, _TransversalStatus],
    issue_check: module_utils.DashboardIssue,
) -> None:
    repo = context.github_project.repo
    try:
        security_file = repo.get_contents("SECURITY.md")
        assert isinstance(security_file, github.ContentFile.ContentFile)
        security = security_md.Security(security_file.decoded_content.decode("utf-8"))

        error_message = audit_utils.outdated_versions(security)
        _process_error(context, _OUTDATED, issue_check, error_message)
    except github.GithubException as exception:
        if exception.status == 404:
            _LOGGER.debug("No SECURITY.md file in the repository")
            _process_error(context, _OUTDATED, issue_check, message="No SECURITY.md file in the repository")
        else:
            _LOGGER.exception("Error while getting SECURITY.md")
            _process_error(context, _OUTDATED, issue_check, message="Error while getting SECURITY.md")
            raise


async def _process_snyk_dpkg(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData, _TransversalStatus],
    issue_check: module_utils.DashboardIssue,
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
        branch: str = cast(str, context.module_event_data.version)

        async with module_utils.WORKING_DIRECTORY_LOCK:
            # Checkout the right branch on a temporary directory
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                _LOGGER.debug("Clone the repository in the temporary directory: %s", tmpdirname)
                success &= await module_utils.git_clone(context.github_project, branch)
                if not success:
                    return ["Fail to clone the repository"], success

                local_config: configuration.AuditConfiguration = {}
                if context.module_event_data.type in ("snyk", "dpkg") and os.path.exists(".github/ghci.yaml"):
                    with open(".github/ghci.yaml", encoding="utf-8") as file:
                        local_config = yaml.load(file, Loader=yaml.SafeLoader).get("audit", {})

                logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
                if context.module_event_data.type == "snyk":
                    python_version = ""
                    if os.path.exists(".tool-versions"):
                        with open(".tool-versions", encoding="utf-8") as file:
                            for line in file:
                                if line.startswith("python "):
                                    python_version = ".".join(line.split(" ")[1].split(".")[0:2]).strip()
                                    break

                    env = await _use_python_version(python_version) if python_version else os.environ.copy()

                    result, body, short_message, new_success = await audit_utils.snyk(
                        branch,
                        context.module_config.get("snyk", {}),
                        local_config.get("snyk", {}),
                        logs_url,
                        env,
                    )
                    body_md = body.to_markdown() if body is not None else ""
                    del body
                    success &= new_success
                    output_url = _process_error(
                        context,
                        key,
                        issue_check,
                        [{"title": m.title, "children": [m.to_html("no-title")]} for m in result],
                        ", ".join(short_message),
                    )
                    message: module_utils.Message = module_utils.HtmlMessage(
                        f"<a href='{output_url}'>Output</a>"
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

                    if os.path.exists("ci/dpkg-versions.yaml") or os.path.exists(
                        ".github/dpkg-versions.yaml"
                    ):
                        await audit_utils.dpkg(
                            context.module_config.get("dpkg", {}), local_config.get("dpkg", {})
                        )

                body_md += "\n" if body_md else ""
                body_md += f"[Logs]({logs_url})"
                short_message.append(f"[Logs]({logs_url})")

                command = ["git", "diff", "--quiet"]
                diff_proc = await asyncio.create_subprocess_exec(*command)
                stdout, stderr = await asyncio.wait_for(diff_proc.communicate(), timeout=30)
                if diff_proc.returncode != 0:
                    command = ["git", "diff"]
                    proc = await asyncio.create_subprocess_exec(
                        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                    message = module_utils.AnsiProcessMessage.from_async_artifacts(
                        command, proc, stdout, stderr
                    )
                    message.title = "Changes to be committed"
                    _LOGGER.debug(message)

                    command = ["git", "checkout", "-b", new_branch]
                    proc = await asyncio.create_subprocess_exec(
                        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                    if proc.returncode != 0:
                        message = module_utils.AnsiProcessMessage.from_async_artifacts(
                            command, proc, stdout, stderr
                        )
                        message.title = "Error while creating the new branch"
                        _LOGGER.error(message)

                    else:
                        new_success, pull_request = await module_utils.create_commit_pull_request(
                            branch,
                            new_branch,
                            f"Audit {key}",
                            body_md,
                            context.github_project,
                        )
                        success &= new_success
                        if not new_success:
                            _LOGGER.error("Error while create commit or pull request")
                        else:
                            if pull_request is not None:
                                issue_check.set_title(key, f"{key} ([Pull request]({pull_request.html_url}))")
                                short_message.append(f"[Pull request]({pull_request.html_url})")

                else:
                    _LOGGER.debug("No changes to commit")
                    module_utils.close_pull_request_issues(new_branch, f"Audit {key}", context.github_project)

        full_repo = f"{context.github_project.owner}/{context.github_project.repository}"
        transversal_message = ", ".join(short_message)
        context.transversal_status.repositories.setdefault(
            full_repo,
            _TransversalStatusRepo(),
        ).types[key] = _TransversalStatusTool(
            title=f"{key}: {transversal_message}",
        )

    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as proc_error:
        message = module_utils.AnsiProcessMessage(
            cast(list[str], proc_error.args),
            None if isinstance(proc_error, subprocess.TimeoutExpired) else proc_error.returncode,
            proc_error.output,
            cast(str, proc_error.stderr),
        )
        _LOGGER.exception("Audit %s process error", key)
        return [f"Error while processing the audit {key}: {proc_error}"], False
    except Exception as exception:  # pylint: disable=broad-except
        _LOGGER.exception("Audit %s error", key)
        return [f"Error while processing the audit {key}: {exception}"], False
    finally:
        os.chdir("/")

    return short_message, success


async def _use_python_version(python_version: str) -> dict[str, str]:
    command = ["pyenv", "local", python_version]
    proc = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
    message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
    if proc.returncode != 0:
        message.title = f"Error while setting the Python version to {python_version}"
        _LOGGER.error(message)
    else:
        message.title = f"Setting the Python version to {python_version}"
        _LOGGER.debug(message)
    command = ["python", "--version"]
    proc = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)

    # Get path from /pyenv/versions/{python_version}.*/bin/
    env = os.environ.copy()
    bin_paths = glob.glob(f"/pyenv/versions/{python_version}.*/bin")
    if bin_paths:
        env["PATH"] = f'{bin_paths[0]}:{env["PATH"]}'

    message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
    message.title = "Python version"
    _LOGGER.debug(message)

    # Cleanup the packages
    shutil.rmtree(f"/var/www/.local/lib/python{python_version}", ignore_errors=True)

    return env


class Audit(module.Module[configuration.AuditConfiguration, _EventData, _TransversalStatus]):
    """The audit module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Audit (Snyk/dpkg)"

    def description(self) -> str:
        """Get the description of the module."""
        return "Audit the project with Snyk (for CVE in dependency) and update dpkg package version to trigger a rebuild"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Audit"

    def required_issue_dashboard(self) -> bool:
        """Check if the module requires an issue dashboard."""
        return True

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if "SECURITY.md" in context.event_data.get("push", {}).get("files", []):
            return [
                module.Action(
                    priority=module.PRIORITY_CRON, data=_EventData(type="outdated"), title="outdated"
                )
            ]
        results: list[module.Action[_EventData]] = []
        snyk = False
        dpkg = False
        is_dashboard = context.event_name == "dashboard"
        if is_dashboard:
            old_check = module_utils.DashboardIssue(
                context.event_data.get("old_data", "").split("<!---->")[0]
            )
            new_check = module_utils.DashboardIssue(
                context.event_data.get("new_data", "").split("<!---->")[0]
            )

            if not old_check.is_checked("outdated") and new_check.is_checked("outdated"):
                results.append(
                    module.Action(
                        priority=module.PRIORITY_STANDARD, data=_EventData(type="outdated"), title="outdated"
                    )
                )
            if not old_check.is_checked("snyk") and new_check.is_checked("snyk"):
                snyk = True
            if not old_check.is_checked("dpkg") and new_check.is_checked("dpkg"):
                dpkg = True

        if context.event_data.get("type") == "event" and context.event_data.get("name") == "daily":
            results.append(
                module.Action(
                    priority=module.PRIORITY_CRON, data=_EventData(type="outdated"), title="outdated"
                )
            )
            snyk = True
            dpkg = True

        if dpkg or snyk:
            results.append(
                module.Action(
                    priority=module.PRIORITY_CRON,
                    data=_EventData(snyk=snyk, dpkg=dpkg, is_dashboard=is_dashboard),
                )
            )
        return results

    async def process(
        self, context: module.ProcessContext[configuration.AuditConfiguration, _EventData, _TransversalStatus]
    ) -> module.ProcessOutput[_EventData, _TransversalStatus]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        issue_check = module_utils.DashboardIssue(context.issue_data)
        repo = context.github_project.repo
        short_message: list[str] = []
        success = True

        module_utils.manage_updated_separated(
            context.transversal_status.updated,
            context.transversal_status.repositories,
            f"{context.github_project.owner}/{context.github_project.repository}",
        )

        # If no SECURITY.md apply on default branch
        key_starts = []
        security_file = None
        try:
            security_file = repo.get_contents("SECURITY.md")
        except github.GithubException as exception:
            if exception.status == 404:
                _LOGGER.debug("No security file in the repository")
            else:
                raise
        if security_file is not None:
            key_starts.append(_OUTDATED)
            issue_check.add_check("outdated", "Check outdated version", False)
        else:
            issue_check.remove_check("outdated")

        if context.module_config.get("snyk", {}).get("enabled", configuration.ENABLE_SNYK_DEFAULT):
            issue_check.add_check("snyk", "Check security vulnerabilities with Snyk", False)
            key_starts.append("Snyk check/fix ")
        else:
            issue_check.remove_check("snyk")

        dpkg_version = None
        try:
            dpkg_version = repo.get_contents(".github/dpkg-versions.yaml")
        except github.GithubException as exception:
            if exception.status == 404:
                try:
                    dpkg_version = repo.get_contents("ci/dpkg-versions.yaml")
                except github.GithubException as exception2:
                    if exception2.status == 404:
                        _LOGGER.debug("No dpkg-versions.yaml file in the repository")
                    else:
                        raise
            else:
                raise
        if (
            context.module_config.get("dpkg", {}).get("enabled", configuration.ENABLE_DPKG_DEFAULT)
            and dpkg_version is not None
        ):
            issue_check.add_check("dpkg", "Update dpkg packages", False)
            key_starts.append("Dpkg ")
        else:
            issue_check.remove_check("dpkg")

        if context.module_event_data.type == "outdated":
            _process_outdated(context, issue_check)
        else:
            if context.module_event_data.version is None:
                # Creates new jobs with the versions from the SECURITY.md
                versions = []
                if security_file is not None:
                    assert isinstance(security_file, github.ContentFile.ContentFile)
                    security = security_md.Security(security_file.decoded_content.decode("utf-8"))

                    versions = module_utils.get_stabilization_versions(security)
                else:
                    _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")
                    versions = [repo.default_branch]
                _LOGGER.debug("Versions: %s", ", ".join(versions))

                all_key_starts = []
                for key in key_starts:
                    if key == _OUTDATED:
                        all_key_starts.append(_OUTDATED)
                    else:
                        for version in versions:
                            all_key_starts.append(f"{key}{version}")

                full_repository = f"{context.github_project.owner}/{context.github_project.repository}"
                if full_repository in context.transversal_status.repositories:
                    for key in list(context.transversal_status.repositories[full_repository].types.keys()):
                        if key not in all_key_starts:
                            context.transversal_status.repositories[full_repository].types.pop(key)

                # Audit is relay slow than add 15 to the cron priority
                priority = (
                    module.PRIORITY_STANDARD
                    if context.module_event_data.is_dashboard
                    else module.PRIORITY_CRON + 15
                )
                actions = []
                for version in versions:
                    version = context.module_config.get("version-mapping", {}).get(version, version)
                    if context.module_event_data.snyk and context.module_config.get("snyk", {}).get(
                        "enabled", configuration.ENABLE_SNYK_DEFAULT
                    ):
                        actions.append(
                            module.Action(
                                priority=priority,
                                data=_EventData(type="snyk", version=version),
                                title=f"snyk ({version})",
                            )
                        )
                    if context.module_event_data.dpkg and context.module_config.get("dpkg", {}).get(
                        "enabled", configuration.ENABLE_DPKG_DEFAULT
                    ):
                        actions.append(
                            module.Action(
                                priority=priority,
                                data=_EventData(type="dpkg", version=version),
                                title=f"dpkg ({version})",
                            )
                        )
                return ProcessOutput(actions=actions, transversal_status=context.transversal_status)
            else:
                short_message, success = await _process_snyk_dpkg(context, issue_check)

        return _get_process_output(context, issue_check, short_message, success)

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
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
        self, context: module.TransversalDashboardContext[_TransversalStatus]
    ) -> module.TransversalDashboardOutput:
        """Get the transversal dashboard content."""
        repositories = []
        for repository, data in context.status.repositories.items():
            if data:
                repositories.append(
                    {
                        "repository": repository,
                        "data": data,
                    }
                )
        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/audit/dashboard.html",
            data={"repositories": repositories},
        )
