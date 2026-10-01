# Copyright (c) 2026, Camptocamp SA

"""the audit modules."""

import asyncio
import base64
import datetime
import json
import logging
import os
import re
import shutil
import urllib.parse
from typing import Any, Literal, cast

import anyio
import githubkit.exception
import githubkit.webhooks
import githubkit_schemas.latest.models
import security_md
import sqlalchemy
import yaml
from githubkit.compat import type_validate_python
from githubkit_schemas.v2026_03_10.models import RepositoryAdvisory
from githubkit_schemas.v2026_03_10.types import (
    RepositoryAdvisoryCreatePropVulnerabilitiesItemsPropPackageType,
    RepositoryAdvisoryCreatePropVulnerabilitiesItemsType,
    RepositoryAdvisoryCreateType,
)
from multi_repo_automation import aio_editor as editor
from pydantic import BaseModel

from github_app_geo_project import models, module
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration
from github_app_geo_project.module.audit import utils as audit_utils
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)

# Don't run Snyk in parallel
_SNYK_LOCK = asyncio.Lock()

_OUTDATED = "Outdated version"
_CLEANUP = "Cleanup"
_ADVISORY = False

_PRIORITY_CLEANUP = module.PRIORITY_STANDARD + 1
"""Priority of the audit `cleanup` jobs (fast, API calls only)."""
_PRIORITY_OUTDATED = module.PRIORITY_STANDARD + 2
"""Priority of the audit `outdated` jobs (fast, reads the `SECURITY.md` file)."""
_PRIORITY_RENOVATE = module.PRIORITY_STANDARD + 3
"""Priority of the audit `renovate` fan-out jobs."""
_PRIORITY_FAN_OUT = module.PRIORITY_CRON
"""Priority of the audit Snyk/dpkg fan-out parent jobs (short)."""
_PRIORITY_RENOVATE_VERSION = module.PRIORITY_CRON + 1
"""Priority of the audit per-version `renovate` jobs."""
_PRIORITY_DPKG = module.PRIORITY_CRON + 2
"""Priority of the audit `dpkg` jobs."""
_PRIORITY_SNYK = module.PRIORITY_CRON + 3
"""Priority of the audit `snyk` jobs (the slowest, serialized by `_SNYK_LOCK`)."""


class _TransversalStatusTool(BaseModel):
    """Status data for a single check type, stored in transversal status."""

    name: str = ""
    summary: str = ""
    status: str = ""
    logs_url: str | None = None
    output_url: str | None = None


class _VulnerabilityStatus(BaseModel):
    """Vulnerability data stored in the output."""

    file: str
    package_name: str
    package_version: str
    package_manager: str
    severity: str
    snyk_id: str
    cve_ids: list[str] = []
    cwe_ids: list[str] = []
    fixed_in: list[str] = []
    is_upgradable: bool = False
    is_patchable: bool = False
    reason: str = ""
    """The reason the vulnerability is ignored (from .snyk file)."""


class _OutputRendererData(BaseModel):
    """Output renderer data for the audit module stored in the output."""

    branch: str
    vulnerabilities: dict[str, list[_VulnerabilityStatus]]
    ignored_vulnerabilities: dict[str, list[_VulnerabilityStatus]]
    low_severity_vulnerabilities: dict[str, list[_VulnerabilityStatus]]


def _vulnerability_status(
    vulnerability: audit_utils.VulnerabilityData,
    reason: str = "",
) -> _VulnerabilityStatus:
    """Convert a Snyk vulnerability to the data stored in the output."""
    return _VulnerabilityStatus(
        file=vulnerability.file,
        package_name=vulnerability.package_name,
        package_version=vulnerability.package_version,
        package_manager=vulnerability.package_manager,
        severity=vulnerability.severity,
        snyk_id=vulnerability.snyk_id,
        cve_ids=vulnerability.cve_ids,
        cwe_ids=vulnerability.cwe_ids,
        fixed_in=vulnerability.fixed_in,
        is_upgradable=vulnerability.is_upgradable,
        is_patchable=vulnerability.is_patchable,
        reason=reason,
    )


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
    known_types: list[str] | None = None
    """When set, the transversal status types that are not in this list are pruned."""


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
        check_output={"summary": "\n".join(short_message)} if short_message else {},
    )


async def _process_error(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    key: str,
    issue_check: module_utils.DashboardIssue,
    error_message: list[str | models.OutputData] | None = None,
    message: str | None = None,
) -> _TransversalStatusTool:
    logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
    if error_message:
        issue_check.set_title(
            key,
            (f"{key}: {message} ([Logs]({logs_url}))" if message else f"{key} ([Logs]({logs_url}))"),
        )
    elif message:
        issue_check.set_title(key, f"{key}: {message} ([Logs]({logs_url}))")
    else:
        issue_check.set_title(
            key,
            f"{key}: everything is fine ([Logs]({logs_url}))",
        )

    return _TransversalStatusTool(
        name=key, summary=message or "", status="error" if error_message else "success", logs_url=logs_url
    )


def _remove_dashboard_vuln_section(issue_check: module_utils.DashboardIssue, version: str) -> bool:
    """
    Remove the legacy vulnerability section of a version from the dashboard issue.

    Returns True if a section was found and removed.
    """
    vuln_section_start = f"<!-- vulns-{version} -->"
    vuln_section_end = f"<!-- /vulns-{version} -->"
    vuln_title = f"=== {version}"
    found_start = None
    found_end = None
    for i, item in enumerate(issue_check.issue):
        if isinstance(item, str) and item in {vuln_section_start, vuln_title}:
            found_start = i
        if isinstance(item, str) and item == vuln_section_end:
            found_end = i
            break
    if found_start is not None and found_end is not None:
        del issue_check.issue[found_start : found_end + 1]
    elif found_start is not None:
        # New format: remove from the title to the end or next ===
        section_end = len(issue_check.issue)
        for j in range(found_start + 1, len(issue_check.issue)):
            item = issue_check.issue[j]
            if isinstance(item, str) and item.startswith("==="):
                section_end = j
                break
        del issue_check.issue[found_start:section_end]
    else:
        return False
    return True


def _dashboard_vuln_section_versions(issue_check: module_utils.DashboardIssue) -> set[str]:
    """Get the versions that have a legacy vulnerability section in the dashboard issue."""
    versions: set[str] = set()
    for item in issue_check.issue:
        if isinstance(item, str):
            if item.startswith("=== "):
                versions.add(item[len("=== ") :].strip())
            elif item.startswith("<!-- vulns-") and item.endswith(" -->"):
                versions.add(item[len("<!-- vulns-") : -len(" -->")])
    return versions


def _details_markdown(summary: str, content: str) -> str:
    """Build a collapsed markdown section, used for the raw fix command output."""
    return f"<details>\n<summary>{summary}</summary>\n\n{content}\n\n</details>"


def _fixed_vulnerabilities_markdown(
    fixed_vulnerabilities: dict[str, list[audit_utils.VulnerabilityData]],
) -> str:
    """Build the markdown list of the vulnerabilities fixed by the current audit run."""
    if not fixed_vulnerabilities:
        return ""
    lines = ["## Fixed vulnerabilities", ""]
    for file_name, vulnerabilities in fixed_vulnerabilities.items():
        for vulnerability in vulnerabilities:
            identifiers = [
                *[
                    f"[{cve_id}](https://nvd.nist.gov/vuln/detail/{cve_id})"
                    for cve_id in vulnerability.cve_ids
                ],
                f"[{vulnerability.snyk_id}](https://security.snyk.io/vuln/{vulnerability.snyk_id})",
            ]
            line = (
                f"- **[{vulnerability.severity.upper()}]** `{vulnerability.package_name}`"
                f" `{vulnerability.package_version}` in `{file_name}`"
                f" — {', '.join(identifiers)}"
            )
            if vulnerability.fixed_in:
                line += f" — fixed in {', '.join(f'`{version}`' for version in vulnerability.fixed_in)}"
            lines.append(line)
    return "\n".join(lines)


async def _process_renovate(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    known_versions: list[str] | None,
) -> bool:
    """
    Process Renovate configuration updates.

    Args:
        context: ProcessContext containing:
            - module_event_data: Event data for the module (_EventData)
            - github_project: GitHub project information
            - module_config: Module configuration (AuditConfiguration)
            - service_url: Service URL for generating links
            - job_id: Job ID for logging
        known_versions: List of known versions to update in baseBranchPatterns

    Returns
    -------
        bool: True if successful, False otherwise
    """
    if context.module_event_data.version is None:
        _LOGGER.debug("Process renovate update on default branch")

        assert known_versions is not None

        default_branch = await context.github_project.default_branch()

        async with module_utils.GIT_WORKTREE_CACHE.working_tree(
            context.github_project,
            default_branch,
        ) as new_cwd:
            renovate_config_path = new_cwd / ".github" / "renovate.json5"
            if await renovate_config_path.exists():
                async with editor.EditRenovateConfig(renovate_config_path) as renovate_config:
                    # Add other versions to baseBranchPatterns, avoiding duplicates
                    other_versions = [v for v in known_versions if v != default_branch]
                    if other_versions:
                        renovate_config["baseBranchPatterns"] = [default_branch, *other_versions]
                    elif "baseBranchPatterns" in renovate_config:
                        # Remove baseBranchPatterns if it only contains the default branch
                        del renovate_config["baseBranchPatterns"]

            logs_url = urllib.parse.urljoin(
                context.service_url,
                f"logs/{context.job_id}",
            )
            success, _ = await _create_pull_request_if_changes(
                default_branch,
                f"ghci/audit/renovate/{default_branch}",
                "Update Renovate configuration",
                f"Update the stabilization branches in the Renovate configuration\n\n[Logs]({logs_url})",
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

    # Never process cleanup on the default branch
    default_branch = await context.github_project.default_branch()
    if context.module_event_data.version == default_branch:
        _LOGGER.debug(
            "Skipping Renovate cleanup for default branch %s",
            default_branch,
        )
        return True

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        context.module_event_data.version,
    ) as new_cwd:
        renovate_config_path = new_cwd / ".github" / "renovate.json5"
        if await renovate_config_path.exists():
            await renovate_config_path.unlink()
        security_md_path = new_cwd / "SECURITY.md"
        if await security_md_path.exists():
            await security_md_path.unlink()

        logs_url = urllib.parse.urljoin(
            context.service_url,
            f"logs/{context.job_id}",
        )
        success, _ = await _create_pull_request_if_changes(
            context.module_event_data.version,
            f"ghci/audit/renovate/{context.module_event_data.version}",
            f"Cleanup Renovate configuration for version {context.module_event_data.version}",
            f"Remove the Renovate configuration and the SECURITY.md file if they exist\n\n[Logs]({logs_url})",
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
        assert isinstance(security_file, githubkit_schemas.latest.models.ContentFile)
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
    output_tool = _TransversalStatusTool()

    key = f"Undefined {context.module_event_data.version}"
    new_branch = f"ghci/audit/{context.module_event_data.type}/{context.module_event_data.version}"
    if context.module_event_data.type == "snyk":
        key = f"Snyk check/fix {context.module_event_data.version}"
    if context.module_event_data.type == "dpkg":
        key = f"Dpkg {context.module_event_data.version}"
    branch: str = cast("str", context.module_event_data.version)

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        branch,
    ) as cwd:
        local_config: configuration.AuditConfiguration = {}

        ghci_config_path = cwd / ".github" / "ghci.yaml"
        if context.module_event_data.type in ("snyk", "dpkg") and await ghci_config_path.exists():
            async with await ghci_config_path.open("r", encoding="utf-8") as file:
                local_config = yaml.load(
                    await file.read(),
                    Loader=yaml.SafeLoader,
                ).get("audit", {})

        logs_url = urllib.parse.urljoin(
            context.service_url,
            f"logs/{context.job_id}",
        )
        if context.module_event_data.type == "snyk":
            async with _SNYK_LOCK:
                python_version = ""
                tool_versions = cwd / ".tool-versions"
                if await tool_versions.exists():
                    async with await tool_versions.open("r", encoding="utf-8") as file:
                        for line in (await file.read()).splitlines():
                            if line.startswith("python "):
                                python_version = ".".join(
                                    line.split(" ")[1].split(".")[0:2],
                                ).strip()
                                break

                env = await _use_python_version(python_version, cwd) if python_version else os.environ.copy()

                (
                    result,
                    body,
                    short_message,
                    new_success,
                    file_vulnerabilities,
                    fixed_vulns,
                ) = await audit_utils.snyk(
                    branch,
                    context.github_project.owner,
                    context.github_project.repository,
                    context.module_config,
                    local_config,
                    context.module_config.get("snyk", {}),
                    local_config.get("snyk", {}),
                    logs_url,
                    env,
                    cwd,
                )

                # Run a second Snyk JSON test with --ignore-policy to find ignored vulnerabilities
                ignored_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
                all_file_vulns = await audit_utils.snyk_test_ignored(
                    branch,
                    context.module_config.get("snyk", {}),
                    local_config.get("snyk", {}),
                    env,
                    cwd,
                )

                # Build a set of (snyk_id, package_version, file) for non-ignored vulns
                non_ignored_keys: set[tuple[str, str, str]] = set()
                for file_name, vulns in file_vulnerabilities.items():
                    for vuln in vulns:
                        non_ignored_keys.add((vuln.snyk_id, vuln.package_version, file_name))

                # Find vulns present in all but not in non-ignored → these are ignored
                for file_name, vulns in all_file_vulns.items():
                    for vuln in vulns:
                        vuln_key = (vuln.snyk_id, vuln.package_version, file_name)
                        if vuln_key not in non_ignored_keys:
                            ignored_vulns.setdefault(file_name, []).append(vuln)

                # Parse .snyk files for ignore reasons
                snyk_ignore_reasons: dict[str, str] = {}
                snyk_files = await audit_utils.find_snyk_files(cwd)
                for snyk_file in snyk_files:
                    reasons = await audit_utils.parse_snyk_ignore_reasons(snyk_file)
                    snyk_ignore_reasons.update(reasons)

            body_md = _fixed_vulnerabilities_markdown(fixed_vulns)
            if body is not None:
                if body_md:
                    body_md += "\n\n"
                body_md += _details_markdown(body.title or "Fix output", body.to_markdown())
            del body
            success &= new_success
            output_tool = await _process_error(
                context,
                key,
                issue_check,
                [{"title": m.title, "children": [m.to_html("no-title")]} for m in result],
                ", ".join(short_message),
            )
            # Remove old vulnerability section (both old comment format and new format)
            if not _remove_dashboard_vuln_section(issue_check, branch):
                # Remove all str entries (vulnerability lines, separators, module data)
                issue_check.issue = [item for item in issue_check.issue if not isinstance(item, str)]

            # Apply filtering and build new dashboard section
            snyk_config = context.module_config.get("snyk", {})
            local_snyk_config = local_config.get("snyk", {})
            excluded_files = audit_utils.get_excluded_files(snyk_config, local_snyk_config)
            excluded_patterns = [re.compile(p) for p in excluded_files]
            dashboard_threshold = audit_utils.get_severity_config(
                snyk_config,
                local_snyk_config,
                "dashboard-severity-threshold",
                configuration.DASHBOARD_SEVERITY_THRESHOLD_DEFAULT,
            )
            advisory_threshold = audit_utils.get_severity_config(
                snyk_config,
                local_snyk_config,
                "advisory-severity-threshold",
                configuration.ADVISORY_SEVERITY_THRESHOLD_DEFAULT,
            )
            min_dashboard_severity = audit_utils.SEVERITY_ORDER.get(dashboard_threshold, 1)
            min_advisory_severity = audit_utils.SEVERITY_ORDER.get(advisory_threshold, 2)

            # Filter vulnerabilities by excluded files and thresholds
            filtered_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
            low_severity_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
            high_critical_vulns: list[audit_utils.VulnerabilityData] = []
            for file_name, vulns in file_vulnerabilities.items():
                if any(p.search(file_name) for p in excluded_patterns):
                    continue
                for vuln in vulns:
                    vuln_severity = audit_utils.SEVERITY_ORDER.get(vuln.severity, 0)
                    if vuln_severity >= min_dashboard_severity:
                        filtered_vulns.setdefault(file_name, []).append(vuln)
                    else:
                        low_severity_vulns.setdefault(file_name, []).append(vuln)
                    if vuln_severity >= min_advisory_severity:
                        high_critical_vulns.append(vuln)

            vuln_data = {
                file_name: [_vulnerability_status(vuln) for vuln in vulns]
                for file_name, vulns in sorted(filtered_vulns.items())
            }
            ignored_data = {
                file_name: [
                    _vulnerability_status(vuln, snyk_ignore_reasons.get(vuln.snyk_id, "No reason provided"))
                    for vuln in vulns
                ]
                for file_name, vulns in sorted(ignored_vulns.items())
            }
            low_severity_data = {
                file_name: [_vulnerability_status(vuln) for vuln in vulns]
                for file_name, vulns in sorted(low_severity_vulns.items())
            }
            output_renderer_data = _OutputRendererData(
                branch=branch,
                vulnerabilities=vuln_data,
                ignored_vulnerabilities=ignored_data,
                low_severity_vulnerabilities=low_severity_data,
            )

            # Create output for vulnerabilities, even when everything was fixed,
            # to have a stable output URL to link in the pull request body.
            output_name = f"snyk-{branch}"
            await module_utils.add_output(
                context,
                f"Snyk summary report {branch}",
                output_name,
                "github_app_geo_project:module/audit/output.html",
                status=models.OutputStatus.SUCCESS,
                renderer_data=output_renderer_data,
            )
            output_tool.output_url = urllib.parse.urljoin(
                context.service_url,
                f"output/{context.github_project.owner}/{context.github_project.repository}/{output_name}",
            )
            message: module_utils.Message = module_utils.HtmlMessage(
                f"<a href='{output_tool.output_url}'>Output</a>",
            )
            message.title = "Output URL"
            _LOGGER.debug(message)

            # Create security advisories for HIGH and CRITICAL CVEs
            if high_critical_vulns and _ADVISORY:
                await _create_security_advisories(context, high_critical_vulns)

        if context.module_event_data.type == "dpkg":
            body_md = "Update dpkg packages"

            if (
                await (cwd / "ci" / "dpkg-versions.yaml").exists()
                or await (cwd / ".github" / "dpkg-versions.yaml").exists()
            ):
                await audit_utils.dpkg(
                    context.module_config.get("dpkg", {}),
                    local_config.get("dpkg", {}),
                    cwd,
                )

        body_md += "\n\n" if body_md else ""
        body_md += f"[Logs]({logs_url})"
        if output_tool.output_url:
            body_md += f" | [Output]({output_tool.output_url})"

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
        name=key,
        summary=transversal_message,
        status="success" if success else "error",
        logs_url=logs_url,
        output_url=output_tool.output_url,
    )

    return short_message, success


async def _use_python_version(python_version: str, cwd: anyio.Path) -> dict[str, str]:
    # Lazily install the Python version, pyenv versions are no more installed at image build.
    await module_utils.ensure_pyenv_python(python_version)
    command = ["pyenv", "local", python_version]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(settings.audit.timeouts.pyenv_local.total_seconds()):
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
    async with asyncio.timeout(settings.audit.timeouts.python_version.total_seconds()):
        stdout, stderr = await proc.communicate()

    # Get path from <pyenv root>/versions/{python_version}.*/bin/
    env = os.environ.copy()
    versions_path = anyio.Path(module_utils.get_pyenv_root() / "versions")
    bin_paths = [path async for path in versions_path.glob(f"{python_version}.*/bin")]
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
    await anyio.to_thread.run_sync(
        lambda: shutil.rmtree(f"/var/www/.local/lib/python{python_version}", ignore_errors=True)
    )

    return env


async def _create_pull_request_if_changes(
    branch: str,
    new_branch: str,
    key: str,
    body_md: str,
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    local_config: configuration.AuditConfiguration,
    cwd: anyio.Path,
    issue_check: module_utils.DashboardIssue | None,
) -> tuple[bool, list[str]]:
    """Create a pull request if there are changes to commit."""
    success = True
    short_message: list[str] = []

    command = ["git", "diff", "--quiet"]
    diff_proc = await asyncio.create_subprocess_exec(*command, cwd=cwd)
    try:
        async with asyncio.timeout(settings.audit.timeouts.git_diff.total_seconds()):
            await diff_proc.communicate()
        if diff_proc.returncode != 0:
            command = ["git", "diff"]
            await module_utils.run_timeout(
                command,
                env=None,
                timeout=settings.audit.timeouts.git_diff,
                success_message="Changes to be committed",
                error_message="Git diff failed",
                timeout_message="Git diff timed out",
                cwd=cwd,
                error=False,
            )

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
        except ProcessLookupError:
            _LOGGER.debug(
                "diff_proc already terminated before kill() after timeout; ignoring ProcessLookupError",
            )
        raise

    return success, short_message


def _add_missing_fields(item: dict[str, Any]) -> dict[str, Any]:
    return {**item, "publisher": None, "author": None}


async def _create_security_advisories(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    vulnerabilities: list[audit_utils.VulnerabilityData],
) -> None:
    """Create GitHub Security Advisories for the given vulnerabilities."""
    # List existing advisories to avoid duplicates
    existing_advisory_ids: set[str] = set()
    paginator = context.github_project.aio_github.rest.paginate(
        context.github_project.aio_github.rest.security_advisories.async_list_repository_advisories,
        map_func=lambda response: type_validate_python(
            list[RepositoryAdvisory],
            [_add_missing_fields(item) for item in response.json()],
        ),
        owner=context.github_project.owner,
        repo=context.github_project.repository,
        state="published",
    )
    async for advisory in paginator:
        if advisory.identifiers:
            for identifier in advisory.identifiers:
                if identifier.type == "CVE":
                    existing_advisory_ids.add(identifier.value)
                    break

    for vuln in vulnerabilities:
        cve_id = vuln.cve_ids[0] if vuln.cve_ids else None
        if cve_id and cve_id in existing_advisory_ids:
            _LOGGER.debug("Security advisory already exists for %s", cve_id)
            continue

        try:
            ecosystem = cast(
                "Literal['rubygems', 'npm', 'pip', 'maven', 'nuget', 'composer', 'go', 'rust', 'erlang', 'actions', 'pub', 'other', 'swift']",
                audit_utils.ECOSYSTEM_MAP.get(vuln.package_manager, "other"),
            )

            vulnerability_package = RepositoryAdvisoryCreatePropVulnerabilitiesItemsPropPackageType(
                ecosystem=ecosystem,
                name=vuln.package_name,
            )
            vuln_version_range = f">= {vuln.package_version}"
            patched_versions = ", ".join(vuln.fixed_in) if vuln.fixed_in else None
            vulnerability_item = RepositoryAdvisoryCreatePropVulnerabilitiesItemsType(
                package=vulnerability_package,
                vulnerable_version_range=vuln_version_range,
                patched_versions=patched_versions,
            )

            severity = vuln.severity if vuln.severity in ("critical", "high", "medium", "low") else "high"
            cwe_ids = [cwe for cwe in vuln.cwe_ids if cwe.startswith("CWE-")] or None

            data: RepositoryAdvisoryCreateType = {
                "summary": vuln.package_name if cve_id is None else f"{cve_id} in {vuln.package_name}",
                "description": (
                    f"Vulnerability detected in {vuln.file}:\n\n"
                    f"Package: {vuln.package_name}@{vuln.package_version}\n"
                    f"Snyk ID: {vuln.snyk_id}\n"
                    f"Severity: {vuln.severity}\n"
                    f"File: {vuln.file}"
                ),
                "vulnerabilities": [vulnerability_item],
                "severity": cast(
                    "Literal['critical', 'high', 'medium', 'low'] | None",
                    severity,
                ),
                "cve_id": cve_id,
                "cwe_ids": cwe_ids,
            }
            await context.github_project.aio_github.rest.security_advisories.async_create_repository_advisory(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                data=data,
            )
            if cve_id:
                existing_advisory_ids.add(cve_id)
            _LOGGER.info("Created security advisory for %s", vuln.snyk_id)
        except githubkit.exception.RequestFailed as exception:
            _LOGGER.warning(
                "Failed to create security advisory for %s: %s",
                vuln.snyk_id,
                exception.response.text if exception.response else str(exception),
            )
            raise


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
        return "Audit (Snyk/dpkg/Renovate)"

    def description(self) -> str:
        """Get the description of the module."""
        return "Audit the project with Snyk (for CVE in dependency) and update dpkg package version to trigger a rebuild, also update the Renovate configuration"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/audit/README.md"

    def required_issue_dashboard(self) -> bool:
        """Check if the module requires an issue dashboard."""
        return True

    def jobs_unique_on(self) -> list[module.Fields] | None:
        """Return the list of fields that should be unique for the jobs."""
        return [module.Fields.OWNER, module.Fields.REPOSITORY, module.Fields.MODULE_EVENT_NAME]

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.module_event_name == "pull_request":
            event_data_pull_request = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            if event_data_pull_request.action == "closed":
                return [
                    module.Action(
                        priority=module.PRIORITY_STANDARD,
                        data=_EventData(type="close-pull-request-issues"),
                        title=f"close-pull-request-issues ({event_data_pull_request.pull_request.number})",
                    )
                ]

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
                            priority=_PRIORITY_CLEANUP,
                            data=_EventData(type="cleanup"),
                            title="cleanup",
                        ),
                    ]

                if "SECURITY.md" in [
                    *(commit.modified or []),
                    *(commit.added or []),
                ]:
                    if event_data_push.ref == f"refs/heads/{event_data_push.repository.default_branch}":
                        return [
                            module.Action(
                                priority=_PRIORITY_OUTDATED,
                                data=_EventData(type="outdated"),
                                title="outdated",
                            ),
                            module.Action(
                                priority=_PRIORITY_RENOVATE,
                                data=_EventData(type="renovate"),
                                title="renovate",
                            ),
                        ]
                    return [
                        module.Action(
                            priority=_PRIORITY_OUTDATED,
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
                        priority=_PRIORITY_OUTDATED,
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
                    priority=_PRIORITY_OUTDATED,
                    data=_EventData(type="outdated"),
                    title="outdated",
                )
            )
            results.append(
                module.Action(
                    priority=_PRIORITY_RENOVATE,
                    data=_EventData(type="renovate"),
                    title="renovate",
                )
            )
            snyk = True
            dpkg = True

        if dpkg or snyk:
            results.append(
                module.Action(
                    priority=_PRIORITY_FAN_OUT,
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
        if (
            context.github_event_data.get("type") == "event"
            and context.github_event_data.get("name") == "daily"
        ):
            response = await context.github_project.aio_github.rest.repos.async_get(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
            )
            if response.parsed_data.archived:
                _LOGGER.warning(
                    "Repository %s/%s is archived, skipping audit cron",
                    context.github_project.owner,
                    context.github_project.repository,
                )
                return module.ProcessOutput(success=True)

        issue_check = module_utils.DashboardIssue(context.issue_data)
        short_message: list[str] = []
        success = True
        intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())

        if context.module_event_data.type == "close-pull-request-issues":
            event_data_pull_request = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            await module_utils.close_pull_request_related_issues(
                context.github_project,
                event_data_pull_request.pull_request.number,
                event_data_pull_request.pull_request.title,
            )
            return module.ProcessOutput(success=True)

        # Handle cleanup when SECURITY.md is removed on default branch
        if context.module_event_data.type == "cleanup":
            _LOGGER.info("Cleaning up audit-related pull requests and issues")
            known_versions = context.module_event_data.known_versions or []
            cleaned: list[str] = []
            # Close all audit-related pull requests
            async for branch in context.github_project.aio_github.paginate(
                context.github_project.aio_github.rest.repos.async_list_branches,
                owner=context.github_project.owner,
                repo=context.github_project.repository,
            ):
                branch_name = branch.name
                for key_prefix in ["snyk", "dpkg", "renovate"]:
                    if branch_name.startswith(f"ghci/audit/{key_prefix}/"):
                        version = branch_name.split("/", 3)[-1]
                        if version not in known_versions:
                            _LOGGER.debug("Closing pull requests for branch %s", branch_name)
                            issue_message = (
                                f"Audit Snyk check/fix {version}"
                                if key_prefix == "snyk"
                                else f"Audit Dpkg {version}"
                                if key_prefix == "dpkg"
                                else f"Audit Cleanup Renovate configuration for version {version}"
                            )
                            await module_utils.close_pull_request_issues(
                                branch_name,
                                issue_message,
                                context.github_project,
                            )
                            cleaned.append(f"branch and pull requests of `{branch_name}`")

            renovate_issue_prefix = "Pull request Audit Cleanup Renovate configuration for version "
            issue: githubkit_schemas.latest.models.Issue
            async for issue in context.github_project.aio_github.paginate(
                context.github_project.aio_github.rest.issues.async_list_for_repo,
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                state="open",
                creator=f"{context.github_project.application.slug}[bot]",
            ):
                issue_title: str = issue.title
                issue_version: str | None = None
                for key_prefix in ["Snyk check/fix", "Dpkg"]:
                    prefix = f"Pull request Audit {key_prefix} "
                    if issue_title.startswith(prefix):
                        issue_version = issue_title[len(prefix) :].split(" ", 1)[0]
                        break
                if issue_version is None and issue_title.startswith(renovate_issue_prefix):
                    issue_version = issue_title[len(renovate_issue_prefix) :].split(" ", 1)[0]
                if issue_version is not None and issue_version not in known_versions:
                    _LOGGER.debug("Closing issue %s", issue.html_url)
                    await context.github_project.aio_github.rest.issues.async_update(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        issue_number=issue.number,
                        state="closed",
                    )
                    cleaned.append(f"issue #{issue.number} ({issue_title})")

            # Remove the Snyk outputs of versions that are not supported anymore
            outputs_result = await context.session.execute(
                sqlalchemy.select(models.Output).where(
                    models.Output.owner == context.github_project.owner,
                    models.Output.repository == context.github_project.repository,
                )
            )
            for output in outputs_result.scalars():
                if output.name.startswith("snyk-") and output.name[len("snyk-") :] not in known_versions:
                    _LOGGER.debug("Deleting output %s", output.name)
                    await context.session.delete(output)
                    cleaned.append(f"output `{output.name}`")
            await context.session.commit()

            # Remove the Snyk projects of the references that are not supported anymore,
            # emptying a reference makes it disappear from the Snyk UI
            cleaned.extend(
                await audit_utils.snyk_cleanup_removed_references(
                    context.github_project.owner,
                    context.github_project.repository,
                    known_versions,
                )
            )

            if not known_versions:
                # Clear all checks from dashboard
                issue_check.remove_check("outdated")
                issue_check.remove_check("snyk")
                issue_check.remove_check("dpkg")

            logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
            if cleaned:
                summary = f"{len(cleaned)} leftover(s) removed"
                check_text = "\n".join(f"- {item}" for item in cleaned)
            else:
                summary = "Everything is clean"
                check_text = None
            intermediate_status.status.types[_CLEANUP] = _TransversalStatusTool(
                name=_CLEANUP,
                summary=summary,
                status="success",
                logs_url=logs_url,
            )
            return module.ProcessOutput(
                dashboard=issue_check.to_string(),
                intermediate_status=intermediate_status,
                updated_transversal_status=True,
                success=True,
                check_output=(
                    {"summary": f"Cleanup: {summary}", "text": check_text}
                    if check_text is not None
                    else {"summary": f"Cleanup: {summary}"}
                ),
            )

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
                    isinstance(security_file, githubkit_schemas.latest.models.ContentFile)
                    and security_file.content is not None
                ):
                    security = security_md.Security(
                        base64.b64decode(security_file.content).decode("utf-8"),
                    )

                    versions = security.branches()
                else:
                    _LOGGER.debug(
                        "No SECURITY.md file in the repository, will attempt to remove baseBranchPatterns from Renovate config if present",
                    )
                    versions = []

                mapped_versions = [
                    context.module_config.get("version-mapping", {}).get(version, version)
                    for version in versions
                ]

                _LOGGER.debug("Versions: %s", ", ".join(versions))
                actions.extend(
                    [
                        module.Action(
                            priority=_PRIORITY_RENOVATE_VERSION,
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
                isinstance(security_file, githubkit_schemas.latest.models.ContentFile)
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
                # Prune all the per-version transversal status entries
                intermediate_status.known_types = [_OUTDATED, _CLEANUP]
                return module.ProcessOutput(
                    actions=[
                        module.Action(
                            priority=_PRIORITY_CLEANUP,
                            data=_EventData(type="cleanup"),
                            title="cleanup",
                        )
                    ],
                    dashboard=issue_check.to_string(),
                    intermediate_status=intermediate_status,
                    updated_transversal_status=True,
                )
            _LOGGER.debug("Versions: %s", ", ".join(versions))

            # Apply version mapping to get the actual branch names used
            mapped_versions = [
                context.module_config.get("version-mapping", {}).get(version, version) for version in versions
            ]

            all_key_starts = []
            for key in key_starts:
                if key == _OUTDATED:
                    all_key_starts.append(_OUTDATED)
                else:
                    all_key_starts.extend([f"{key}{version}" for version in mapped_versions])

            # Remove the legacy dashboard vulnerability sections of versions that are not supported anymore
            for version in sorted(_dashboard_vuln_section_versions(issue_check) - set(mapped_versions)):
                _LOGGER.debug("Removing the dashboard vulnerability section of version %s", version)
                _remove_dashboard_vuln_section(issue_check, version)

            # Prune the transversal status entries of versions that are not supported anymore
            intermediate_status.known_types = [*all_key_starts, _CLEANUP]

            actions = [
                module.Action(
                    priority=_PRIORITY_CLEANUP,
                    data=_EventData(
                        type="cleanup",
                        known_versions=[*mapped_versions, await context.github_project.default_branch()],
                    ),
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
                            priority=_PRIORITY_SNYK,
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
                            priority=_PRIORITY_DPKG,
                            data=_EventData(type="dpkg", version=version),
                            title=f"dpkg ({version})",
                        ),
                    )
            return ProcessOutput(
                actions=actions,
                dashboard=issue_check.to_string(),
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
        existing = transversal_status.repositories.get(key, _TransversalStatusRepo())
        if intermediate_status.known_types is not None:
            for type_key in list(existing.types.keys()):
                if type_key not in intermediate_status.known_types:
                    _LOGGER.debug("Remove the stale transversal status type %s", type_key)
                    del existing.types[type_key]
        existing.types.update(intermediate_status.status.types)
        transversal_status.repositories[key] = existing
        return transversal_status

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        return cast(
            "dict[str, Any]",
            json.loads(
                await (anyio.Path(__file__).parent / "schema.json").read_text(encoding="utf-8"),
            )
            .get("properties", {})
            .get("audit"),
        )

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
                "issues": "write",
                "contents": "write",
                "workflows": "write",
                **({"repository_security_advisories": "write"} if _ADVISORY else {}),
            },
            {"push", "pull_request"},
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
            if not data.types:
                continue

            global_types = []
            branches: dict[str, Any] = {}
            for type_key, type_data in data.types.items():
                if type_key == _OUTDATED or " " not in type_key:
                    global_types.append(
                        {
                            "name": type_key,
                            "summary": type_data.summary,
                            "status": type_data.status,
                            "logs_url": type_data.logs_url,
                            "output_url": type_data.output_url,
                        }
                    )
                else:
                    parts = type_key.rsplit(" ", 1)
                    if len(parts) > 1:
                        branch_name = parts[-1]
                        branch = branches.setdefault(branch_name, {"types": []})
                        branch["types"].append(
                            {
                                "name": type_key,
                                "summary": type_data.summary,
                                "status": type_data.status,
                                "logs_url": type_data.logs_url,
                                "output_url": type_data.output_url,
                            }
                        )

            repositories.append(
                {
                    "repository": repository,
                    "global_types": global_types,
                    "branches": [{"name": b, **branches[b]} for b in sorted(branches.keys())],
                },
            )
        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/audit/dashboard.html",
            data={"repositories": repositories},
        )
