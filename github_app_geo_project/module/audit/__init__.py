# Copyright (c) 2026, Camptocamp SA

"""the audit modules."""

import asyncio
import base64
import datetime as dt
import html
import json
import logging
import os
import re
import shutil
import urllib.parse
from typing import Any, cast

import anyio
import githubkit.exception
import githubkit.webhooks
import githubkit_schemas.latest.models
import security_md
import sqlalchemy
import yaml
from multi_repo_automation import aio_editor as editor
from multi_repo_automation.editor import JSON5Item
from pydantic import BaseModel

from github_app_geo_project import models, module
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration
from github_app_geo_project.module.audit import pull_request as audit_pull_request
from github_app_geo_project.module.audit import utils as audit_utils
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)

# Don't run Snyk in parallel
_SNYK_LOCK = asyncio.Lock()

# Don't run two Trivy scans in parallel (the scans are long and resource hungry,
# the image scan and the dependencies scan share the vulnerability database cache)
_TRIVY_LOCK = asyncio.Lock()

_OUTDATED = "Outdated version"
_TRIVY_KEY = "Trivy images"
"""Prefix of the transversal dashboard check listing the scanned container images."""
_TRIVY_PUBLISHED_KEY = "Trivy published images"
"""Prefix of the transversal dashboard check listing the scanned published container images."""
_TRIVY_FILESYSTEM_KEY = "Trivy filesystem check/fix"
"""Prefix of the transversal dashboard entry and pull request title of the dependencies audit."""
_TRIVY_GRADLE_LOCK_KEY = "Trivy Gradle dependency locking"
"""Title of the pull request enabling Gradle dependency locking on a branch without lock files."""
_CLEANUP = "Cleanup"

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
_PRIORITY_TRIVY = module.PRIORITY_CRON + 4
"""Priority of the audit `trivy` container images scan jobs (serialized by `_TRIVY_LOCK`)."""
_PRIORITY_TRIVY_PUBLISHED = module.PRIORITY_CRON + 5
"""Priority of the audit `trivy-published` published container images scan job (serialized by `_TRIVY_LOCK`)."""
_PRIORITY_TRIVY_FILESYSTEM = module.PRIORITY_CRON + 6
"""Priority of the audit `trivy-filesystem` jobs (serialized by `_TRIVY_LOCK`)."""


class _TransversalStatusTool(BaseModel):
    """Status data for a single check type, stored in transversal status."""

    name: str = ""
    summary: str = ""
    status: str = ""
    logs_url: str | None = None
    output_url: str | None = None
    pull_request_url: str | None = None


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
    id_url: str = ""
    """The URL of the vulnerability page for the main ID, empty for Snyk IDs."""


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
        id_url=vulnerability.id_url,
    )


class _TransversalStatusRepo(BaseModel):
    types: dict[str, _TransversalStatusTool] = {}


class _TransversalStatus(BaseModel):
    """The transversal status."""

    updated: dict[str, dt.datetime] = {}
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
    trivy_filesystem: bool = False
    trivy_published: bool = False
    is_dashboard: bool = False
    version: str | None = None
    known_versions: list[str] | None = None  # for cleanup
    versions: list[str] | None = None  # the supported versions of the published images scan


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


def _vulnerability_identifiers(vulnerability: audit_utils.VulnerabilityData) -> list[str]:
    """Build the markdown links to the CVE and database pages of a vulnerability."""
    if vulnerability.id_url:
        return [
            *[f"[{cve_id}](https://nvd.nist.gov/vuln/detail/{cve_id})" for cve_id in vulnerability.cve_ids],
            f"[{vulnerability.snyk_id}]({vulnerability.id_url})",
        ]
    return [
        *[f"[{cve_id}](https://nvd.nist.gov/vuln/detail/{cve_id})" for cve_id in vulnerability.cve_ids],
        f"[{vulnerability.snyk_id}](https://security.snyk.io/vuln/{vulnerability.snyk_id})",
    ]


def _vulnerability_markdown_line(
    vulnerability: audit_utils.VulnerabilityData,
    file_name: str,
    suffix: str = "",
) -> str:
    """Build a markdown list item for a vulnerability, with an optional suffix."""
    line = (
        f"- **[{vulnerability.severity.upper()}]** `{vulnerability.package_name}`"
        f" `{vulnerability.package_version}` in `{file_name}`"
        f" — {', '.join(_vulnerability_identifiers(vulnerability))}"
    )
    if vulnerability.fixed_in:
        line += f" — fixed in {', '.join(f'`{version}`' for version in vulnerability.fixed_in)}"
    return f"{line}{suffix}"


def _fixed_vulnerabilities_markdown(
    fixed_vulnerabilities: dict[str, list[audit_utils.VulnerabilityData]],
) -> str:
    """Build the markdown list of the vulnerabilities fixed by the current audit run."""
    if not fixed_vulnerabilities:
        return ""
    lines = ["## Fixed vulnerabilities", ""]
    lines.extend(
        _vulnerability_markdown_line(vulnerability, file_name)
        for file_name, vulnerabilities in fixed_vulnerabilities.items()
        for vulnerability in vulnerabilities
    )
    return "\n".join(lines)


def _pinned_vulnerabilities_markdown(
    pinned_vulnerabilities: dict[str, list[audit_utils.PinnedVulnerability]],
    note: str = (
        "The rescan still reports these vulnerabilities because Snyk builds the pip dependency tree\n"
        "from the packages installed in the audit environment, which is not reinstalled after the fix."
    ),
) -> str:
    """Build the markdown list of the vulnerabilities addressed by pins added to the requirements files."""
    if not pinned_vulnerabilities:
        return ""
    lines = [
        "## Fixes applied (pins added to requirements files)",
        "",
        note,
        "",
    ]
    lines.extend(
        _vulnerability_markdown_line(
            item.vulnerability,
            file_name,
            suffix=f" — pinned to `{item.pinned_version}`",
        )
        for file_name, pinned in pinned_vulnerabilities.items()
        for item in pinned
    )
    return "\n".join(lines)


def _skipped_fixes_markdown(skipped_fixes: list[str]) -> str:
    """Build the markdown list of the fixes that were not applied, with their reasons."""
    if not skipped_fixes:
        return ""
    lines = ["## Fixes not applied", ""]
    lines.extend(f"- {skipped}" for skipped in skipped_fixes)
    return "\n".join(lines)


async def _process_renovate(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    known_versions: list[str] | None,
) -> tuple[bool, list[str]]:
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
        tuple[bool, list[str]]: True if successful, False otherwise, and the
        messages to display in the check output
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
                        desired_patterns: list[str] = [default_branch, *other_versions]
                        current_patterns: Any = renovate_config.get("baseBranchPatterns")
                        if isinstance(current_patterns, JSON5Item):
                            current_patterns = current_patterns.data()
                        # Do not rewrite the file when the value is already up to date, the
                        # serialization of the editor does not match the prettier style and
                        # this would only produce a formatting change reverted by the hooks
                        if current_patterns != desired_patterns:
                            renovate_config["baseBranchPatterns"] = desired_patterns
                    elif "baseBranchPatterns" in renovate_config:
                        # Remove baseBranchPatterns if it only contains the default branch
                        del renovate_config["baseBranchPatterns"]

            logs_url = urllib.parse.urljoin(
                context.service_url,
                f"logs/{context.job_id}",
            )
            success, renovate_messages, _ = await _create_pull_request_if_changes(
                default_branch,
                f"ghci/audit/renovate/{default_branch}",
                "Update Renovate configuration",
                f"Update the stabilization branches in the Renovate configuration\n\n[Logs]({logs_url})",
                context,
                {},
                new_cwd,
                None,
            )
            return success, renovate_messages

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
        return True, []

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
        success, renovate_messages, _ = await _create_pull_request_if_changes(
            context.module_event_data.version,
            f"ghci/audit/renovate/{context.module_event_data.version}",
            f"Cleanup Renovate configuration for version {context.module_event_data.version}",
            f"Remove the Renovate configuration and the SECURITY.md file if they exist\n\n[Logs]({logs_url})",
            context,
            {},
            new_cwd,
            None,
        )
        return success, renovate_messages


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
                python_version = await _get_branch_python_version(cwd)

                env = await _use_python_version(python_version, cwd) if python_version else os.environ.copy()

                (
                    result,
                    body,
                    short_message,
                    new_success,
                    file_vulnerabilities,
                    fixed_vulns,
                    pinned_vulns,
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
                    python_version,
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
            pinned_md = _pinned_vulnerabilities_markdown(pinned_vulns)
            if pinned_md:
                if body_md:
                    body_md += "\n\n"
                body_md += pinned_md
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
            min_dashboard_severity = audit_utils.SEVERITY_ORDER.get(dashboard_threshold, 1)

            # Filter vulnerabilities by excluded files and thresholds
            filtered_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
            low_severity_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
            for file_name, vulns in file_vulnerabilities.items():
                if any(p.search(file_name) for p in excluded_patterns):
                    continue
                for vuln in vulns:
                    vuln_severity = audit_utils.SEVERITY_ORDER.get(vuln.severity, 0)
                    if vuln_severity >= min_dashboard_severity:
                        filtered_vulns.setdefault(file_name, []).append(vuln)
                    else:
                        low_severity_vulns.setdefault(file_name, []).append(vuln)

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

        new_success, pr_messages, pull_request_url = await _create_pull_request_if_changes(
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
        # The transversal dashboard summary is plain text, the pull request link is
        # rendered by the dashboard template from `pull_request_url`.
        transversal_message = ", ".join(short_message)
        short_message.extend(pr_messages)

    intermediate_status.status.types[key] = _TransversalStatusTool(
        name=key,
        summary=transversal_message,
        status="success" if success else "error",
        logs_url=logs_url,
        output_url=output_tool.output_url,
        pull_request_url=pull_request_url,
    )

    return short_message, success


async def _process_trivy_filesystem(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
    intermediate_status: _IntermediateStatus,
) -> tuple[list[str], bool]:
    """
    Audit and fix the Python, npm and Gradle dependencies of a stabilization branch with Trivy.

    The scan result is stored as an output page (remaining, ignored and low severity
    vulnerabilities), the applied fixes are pushed as a pull request, and a
    `Trivy filesystem check/fix <version>` entry is added to the transversal dashboard.
    A Gradle project without lock files gets its dependency locking enabled on a separate,
    not auto-merged, pull request.
    """
    assert context.module_event_data.version is not None
    branch: str = context.module_event_data.version
    key = f"{_TRIVY_FILESYSTEM_KEY} {branch}"
    new_branch = f"ghci/audit/trivy-filesystem/{branch}"
    short_message: list[str] = []
    success = True
    output_tool = _TransversalStatusTool()
    pull_request_url: str | None = None

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        branch,
    ) as cwd:
        local_config: configuration.AuditConfiguration = {}
        ghci_config_path = cwd / ".github" / "ghci.yaml"
        if await ghci_config_path.exists():
            async with await ghci_config_path.open("r", encoding="utf-8") as file:
                local_config = yaml.load(
                    await file.read(),
                    Loader=yaml.SafeLoader,
                ).get("audit", {})

        logs_url = urllib.parse.urljoin(
            context.service_url,
            f"logs/{context.job_id}",
        )

        filesystem_config = context.module_config.get("trivy", {}).get("filesystem", {})
        local_filesystem_config = local_config.get("trivy", {}).get("filesystem", {})

        async with _TRIVY_LOCK:
            python_version = await _get_branch_python_version(cwd)
            env = await _use_python_version(python_version, cwd) if python_version else os.environ.copy()

            # Enable Gradle dependency locking on a Gradle project without lock files, in a separate
            # pull request that is never auto-merged: it changes the build behavior and must be reviewed.
            bootstrap_enabled = local_filesystem_config.get(
                "auto-fix",
                filesystem_config.get("auto-fix", configuration.AUTOMATIC_FIX0_DEFAULT),
            )
            if bootstrap_enabled:
                bootstrap_changed, bootstrap_detail = await audit_utils.bootstrap_gradle_lockfile(cwd, env)
                if bootstrap_changed:
                    bootstrap_body = (
                        "Enable [Gradle dependency locking](https://docs.gradle.org/current/userguide/"
                        "dependency_locking.html) on the resolvable runtime and compile configurations "
                        "so that the security audit can detect and fix the vulnerable Java dependencies "
                        "of this repository.\n\n"
                        f"{html.escape(bootstrap_detail)}\n\n"
                        "This pull request changes the build behavior: the dependencies become pinned "
                        "in the generated `gradle.lockfile` files. **It is intentionally not auto-merged**,"
                        " review it before merging; the dependency fixes follow on a later audit run once "
                        "it is merged."
                    )
                    (
                        bootstrap_success,
                        bootstrap_messages,
                        _,
                    ) = await _create_pull_request_if_changes(
                        branch,
                        f"ghci/audit/trivy-gradle-lock/{branch}",
                        f"{_TRIVY_GRADLE_LOCK_KEY} {branch}",
                        bootstrap_body,
                        context,
                        local_config,
                        cwd,
                        issue_check,
                        auto_merge=False,
                    )
                    success &= bootstrap_success
                    short_message.extend(bootstrap_messages)
                    # Restore the base state so the dependency scan and fix run without the bootstrap.
                    await module_utils.run_timeout(
                        ["git", "reset", "--hard", f"origin/{branch}"],
                        None,
                        settings.audit.timeouts.git_reset_hard,
                        "Reset after the Gradle locking bootstrap",
                        "Error resetting after the Gradle locking bootstrap",
                        "Timeout resetting after the Gradle locking bootstrap",
                        cwd,
                    )
                    await module_utils.run_timeout(
                        ["git", "clean", "-ffd"],
                        None,
                        settings.audit.timeouts.git_reset_hard,
                        "Clean after the Gradle locking bootstrap",
                        "Error cleaning after the Gradle locking bootstrap",
                        "Timeout cleaning after the Gradle locking bootstrap",
                        cwd,
                    )

            audit_result = await audit_utils.trivy_filesystem(
                branch,
                filesystem_config,
                local_filesystem_config,
                env,
                cwd,
                python_version,
            )
            # A second scan with the ignore policies bypassed to list the ignored vulnerabilities
            all_file_vulns = await audit_utils.trivy_filesystem_ignored(branch, env, cwd)

        non_ignored_keys: set[tuple[str, str, str]] = set()
        for file_name, vulns in audit_result.vulnerabilities.items():
            for vuln in vulns:
                non_ignored_keys.add((vuln.snyk_id, vuln.package_version, file_name))
        ignored_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
        for file_name, vulns in all_file_vulns.items():
            for vuln in vulns:
                if (vuln.snyk_id, vuln.package_version, file_name) not in non_ignored_keys:
                    ignored_vulns.setdefault(file_name, []).append(vuln)

        trivyignore_reasons: dict[str, str] = {}
        for trivyignore_file in await audit_utils.find_trivyignore_files(cwd):
            trivyignore_reasons.update(await audit_utils.parse_trivyignore_reasons(trivyignore_file))

        body_md = _fixed_vulnerabilities_markdown(audit_result.fixed_vulnerabilities)
        pinned_md = _pinned_vulnerabilities_markdown(
            audit_result.pinned_vulnerabilities,
            note=(
                "The floor pins added to the requirements files force the fixed version when the image\n"
                "installs the dependencies."
            ),
        )
        skipped_md = _skipped_fixes_markdown(audit_result.skipped_fixes)
        for part in (pinned_md, skipped_md):
            if part:
                body_md = f"{body_md}\n\n{part}" if body_md else part
        if audit_result.fix_message is not None:
            details = _details_markdown(
                audit_result.fix_message.title or "Fix output",
                audit_result.fix_message.to_markdown(),
            )
            body_md = f"{body_md}\n\n{details}" if body_md else details

        output_tool = await _process_error(
            context,
            key,
            issue_check,
            [{"title": m.title, "children": [m.to_html("no-title")]} for m in audit_result.messages],
            ", ".join(audit_result.short_messages),
        )
        success &= audit_result.success

        excluded_files = local_filesystem_config.get(
            "excluded-files",
            filesystem_config.get("excluded-files", configuration.EXCLUDED_FILES0_DEFAULT),
        )
        excluded_patterns = [re.compile(pattern) for pattern in excluded_files]
        dashboard_threshold = local_filesystem_config.get(
            "dashboard-severity-threshold",
            filesystem_config.get(
                "dashboard-severity-threshold",
                configuration.DASHBOARD_SEVERITY_THRESHOLD0_DEFAULT,
            ),
        )
        min_dashboard_severity = audit_utils.SEVERITY_ORDER.get(dashboard_threshold, 1)

        filtered_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
        low_severity_vulns: dict[str, list[audit_utils.VulnerabilityData]] = {}
        for file_name, vulns in audit_result.vulnerabilities.items():
            if any(p.search(file_name) for p in excluded_patterns):
                continue
            for vuln in vulns:
                vuln_severity = audit_utils.SEVERITY_ORDER.get(vuln.severity, 0)
                if vuln_severity >= min_dashboard_severity:
                    filtered_vulns.setdefault(file_name, []).append(vuln)
                else:
                    low_severity_vulns.setdefault(file_name, []).append(vuln)

        vuln_data = {
            file_name: [_vulnerability_status(vuln) for vuln in vulns]
            for file_name, vulns in sorted(filtered_vulns.items())
        }
        ignored_data = {
            file_name: [
                _vulnerability_status(
                    vuln,
                    trivyignore_reasons.get(vuln.snyk_id, "No reason provided") or "No reason provided",
                )
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

        # Create the output even when everything was fixed, to have a stable output URL
        output_name = f"trivy-filesystem-{branch}"
        await module_utils.add_output(
            context,
            f"Trivy filesystem summary report {branch}",
            output_name,
            "github_app_geo_project:module/audit/output.html",
            status=models.OutputStatus.SUCCESS,
            renderer_data=output_renderer_data,
        )
        output_tool.output_url = urllib.parse.urljoin(
            context.service_url,
            f"output/{context.github_project.owner}/{context.github_project.repository}/{output_name}",
        )

        body_md += "\n\n" if body_md else ""
        body_md += f"[Logs]({logs_url})"
        if output_tool.output_url:
            body_md += f" | [Output]({output_tool.output_url})"

        new_success, pr_messages, pull_request_url = await _create_pull_request_if_changes(
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
        transversal_message = ", ".join(audit_result.short_messages)
        short_message.extend(audit_result.short_messages)
        short_message.extend(pr_messages)

    intermediate_status.status.types[key] = _TransversalStatusTool(
        name=key,
        summary=transversal_message,
        status="success" if success else "error",
        logs_url=logs_url,
        output_url=output_tool.output_url,
        pull_request_url=pull_request_url,
    )

    return short_message, success


async def _process_trivy(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
    intermediate_status: _IntermediateStatus,
) -> tuple[list[str], bool]:
    """
    Scan the container images used by the ArgoCD applications with Trivy.

    The image scan result is stored as an `Output` page (list of the images of each
    application with their critical vulnerabilities) and a `Trivy images <branch>` entry
    is added to the transversal dashboard, linking the output and the job logs.
    """
    assert context.module_event_data.version is not None
    branch: str = context.module_event_data.version
    key = f"{_TRIVY_KEY} {branch}"
    logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
    output_name = f"trivy-images-{branch}"
    output_url = urllib.parse.urljoin(
        context.service_url,
        f"output/{context.github_project.owner}/{context.github_project.repository}/{output_name}",
    )

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        branch,
    ) as cwd:
        local_config: configuration.AuditConfiguration = {}
        ghci_config_path = cwd / ".github" / "ghci.yaml"
        if await ghci_config_path.exists():
            async with await ghci_config_path.open("r", encoding="utf-8") as file:
                local_config = yaml.load(
                    await file.read(),
                    Loader=yaml.SafeLoader,
                ).get("audit", {})

        async with _TRIVY_LOCK:
            report, messages = await audit_utils.trivy_images(
                branch,
                cwd,
                context.module_config.get("trivy", {}).get("image", {}),
                local_config.get("trivy", {}).get("image", {}),
            )
        for message in messages:
            _LOGGER.info(message)

    # An image that could not be scanned is an error of the scan itself, not a vulnerability:
    # the job is in error only when every scanned image failed to scan.
    fatal = report.scan_errors > 0 and report.scan_errors >= report.image_count
    success = not fatal

    await module_utils.add_output(
        context,
        f"Trivy images report {branch}",
        output_name,
        "github_app_geo_project:module/audit/images.html",
        status=models.OutputStatus.ERROR if fatal else models.OutputStatus.SUCCESS,
        renderer_data=report,
    )

    summary = f"{len(report.applications)} applications, {report.image_count} images, {report.critical_total} critical"
    if report.scan_errors:
        summary += f", {report.scan_errors} scan error(s)"
    short_message = [summary]

    issue_check.set_title(key, f"{key}: {summary} ([Logs]({logs_url}) | [Output]({output_url}))")
    intermediate_status.status.types[key] = _TransversalStatusTool(
        name=key,
        summary=summary,
        status="success" if success else "error",
        logs_url=logs_url,
        output_url=output_url,
    )

    return short_message, success


async def _process_trivy_published(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
    issue_check: module_utils.DashboardIssue,
    intermediate_status: _IntermediateStatus,
) -> tuple[list[str], bool]:
    """
    Scan the container images published by the repository with Trivy.

    The images are read from the ``.github/publish.yaml`` configuration of the checked out
    branch for all the supported versions, resolved to their manifest digest through the
    registry API and scanned once per digest; the tags sharing a digest are reported together.
    The result is stored as a ``trivy-published-<branch>`` output and a
    ``Trivy published images <branch>`` entry is added to the transversal dashboard.
    """
    assert context.module_event_data.version is not None
    branch: str = context.module_event_data.version
    versions = context.module_event_data.versions or [branch]
    key = f"{_TRIVY_PUBLISHED_KEY} {branch}"
    logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
    output_name = f"trivy-published-{branch}"
    output_url = urllib.parse.urljoin(
        context.service_url,
        f"output/{context.github_project.owner}/{context.github_project.repository}/{output_name}",
    )

    async with module_utils.GIT_WORKTREE_CACHE.working_tree(
        context.github_project,
        branch,
    ) as cwd:
        local_config: configuration.AuditConfiguration = {}
        ghci_config_path = cwd / ".github" / "ghci.yaml"
        if await ghci_config_path.exists():
            async with await ghci_config_path.open("r", encoding="utf-8") as file:
                local_config = yaml.load(
                    await file.read(),
                    Loader=yaml.SafeLoader,
                ).get("audit", {})

        async with _TRIVY_LOCK:
            report, messages = await audit_utils.trivy_published_images(
                branch,
                cwd,
                context.module_config.get("trivy", {}).get("image", {}),
                local_config.get("trivy", {}).get("image", {}),
                versions,
                context.github_project.token,
            )
        for message in messages:
            _LOGGER.info(message)

    # An image that could not be scanned is an error of the scan itself, not a vulnerability:
    # the job is in error only when every scanned image failed to scan.
    fatal = 0 < report.image_count <= report.scan_errors
    success = not fatal

    await module_utils.add_output(
        context,
        f"Trivy published images report {branch}",
        output_name,
        "github_app_geo_project:module/audit/published-images.html",
        status=models.OutputStatus.ERROR if fatal else models.OutputStatus.SUCCESS,
        renderer_data=report,
    )

    summary = f"{report.image_count} images ({report.tag_count} tags), {report.critical_total} critical"
    if report.scan_errors:
        summary += f", {report.scan_errors} scan error(s)"
    short_message = [summary]

    issue_check.set_title(key, f"{key}: {summary} ([Logs]({logs_url}) | [Output]({output_url}))")
    intermediate_status.status.types[key] = _TransversalStatusTool(
        name=key,
        summary=summary,
        status="success" if success else "error",
        logs_url=logs_url,
        output_url=output_url,
    )

    return short_message, success


async def _process_trivy_pull_request(
    context: module.ProcessContext[configuration.AuditConfiguration, _EventData],
) -> module.ProcessOutput[_EventData, _IntermediateStatus]:
    """
    Scan the head of the pull request for secrets and misconfigurations with Trivy.

    The findings do not fail the pull request: the check run is completed with the
    ``action_required`` conclusion (orange warning), and is red only when Trivy itself
    failed to run.
    """
    trivy_config = context.module_config.get("trivy", {}).get("pull-request", {})
    if not trivy_config.get("enabled", configuration.ENABLE_TRIVY_PULL_REQUEST_SCAN_DEFAULT):
        return module.ProcessOutput(
            check_output={"summary": "Trivy pull request scan disabled for this repository."},
        )

    event_data_pull_request = githubkit.webhooks.parse_obj(
        "pull_request",
        context.github_event_data,
    )
    succeeded, action_required, summary = await audit_pull_request.scan(
        context.github_project,
        event_data_pull_request.pull_request.number,
        trivy_config,
    )
    return module.ProcessOutput(
        success=succeeded,
        action_required=action_required,
        check_output={"summary": summary},
    )


async def _get_branch_python_version(cwd: anyio.Path) -> str:
    """Get the branch Python major.minor from the `.tool-versions` file, empty when not pinned."""
    tool_versions = cwd / ".tool-versions"
    if not await tool_versions.exists():
        return ""
    async with await tool_versions.open("r", encoding="utf-8") as file:
        for line in (await file.read()).splitlines():
            if line.startswith("python "):
                return ".".join(line.split(" ")[1].split(".")[0:2]).strip()
    return ""


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
    auto_merge: bool = True,
) -> tuple[bool, list[str], str | None]:
    """Create a pull request if there are changes to commit."""
    success = True
    short_message: list[str] = []
    pull_request_url: str | None = None

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
            pull_request_result = await module_utils.create_commit_pull_request(
                branch,
                new_branch,
                f"Audit {key}",
                body_md,
                context.github_project,
                cwd,
                pre_commit_config.get("enabled", True),
                pre_commit_config.get("skip-hooks", []),
                auto_merge=auto_merge,
            )
            success &= pull_request_result.success
            pull_request = pull_request_result.pull_request
            if pull_request_result.status is module_utils.PullRequestStatus.STALE:
                # The pull request is open for too long: the reminder issue is already
                # updated and a warning logged by `create_pull_request`. Report the reason
                # in the check output and the dashboard instead of a generic error.
                assert pull_request is not None
                assert pull_request_result.message is not None
                pull_request_url = pull_request.html_url
                short_message.append(pull_request_result.message)
                if issue_check is not None:
                    issue_check.set_title(
                        key,
                        f"{key}: {pull_request_result.message}",
                    )
            elif pull_request_result.status is module_utils.PullRequestStatus.NO_CHANGES:
                # The pre-commit hooks reverted all the changes, the configuration is up
                # to date, close the pull requests and issues of the previous runs.
                await module_utils.close_pull_request_issues(
                    new_branch,
                    f"Audit {key}",
                    context.github_project,
                )
            elif not pull_request_result.success:
                _LOGGER.error(
                    "Error while create commit or pull request",
                )
            elif pull_request is not None:
                pull_request_url = pull_request.html_url
                if issue_check is not None:
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

    return success, short_message, pull_request_url


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
        return "Audit (Snyk/dpkg/Renovate/Trivy)"

    def description(self) -> str:
        """Get the description of the module."""
        return (
            "Audit the project with Snyk (for CVE in dependency), Trivy (for the container images "
            "and the Python and Gradle dependencies CVEs), update the dpkg package versions to trigger a "
            "rebuild, and update the Renovate configuration"
        )

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/audit/README.md"

    def required_issue_dashboard(self) -> bool:
        """Check if the module requires an issue dashboard."""
        return True

    def check_name_per_action(self) -> bool:
        """Include the module event name in the check run name, one job is created per type and version."""
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
            if event_data_pull_request.action in ("opened", "reopened", "synchronize"):
                return [
                    module.Action(
                        priority=module.PRIORITY_STATUS,
                        data=_EventData(type="trivy-pull-request"),
                        title=f"trivy-pull-request ({event_data_pull_request.pull_request.number})",
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
        trivy_filesystem = False
        trivy_published = False
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
            if not old_check.is_checked("trivy-filesystem") and new_check.is_checked("trivy-filesystem"):
                trivy_filesystem = True
            if not old_check.is_checked("trivy-published") and new_check.is_checked("trivy-published"):
                trivy_published = True

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
            trivy_filesystem = True
            trivy_published = True

        if dpkg or snyk or trivy_filesystem or trivy_published:
            results.append(
                module.Action(
                    priority=_PRIORITY_FAN_OUT,
                    data=_EventData(
                        snyk=snyk,
                        dpkg=dpkg,
                        trivy_filesystem=trivy_filesystem,
                        trivy_published=trivy_published,
                        is_dashboard=is_dashboard,
                    ),
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

        # Scan the pull request with Trivy, reported as an action required check,
        # this job does not touch the issue dashboard
        if context.module_event_data.type == "trivy-pull-request":
            return await _process_trivy_pull_request(context)

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
                for key_prefix in ["snyk", "dpkg", "renovate", "trivy-filesystem", "trivy-gradle-lock"]:
                    if branch_name.startswith(f"ghci/audit/{key_prefix}/"):
                        version = branch_name.split("/", 3)[-1]
                        if version not in known_versions:
                            _LOGGER.debug("Closing pull requests for branch %s", branch_name)
                            issue_message = {
                                "snyk": f"Audit Snyk check/fix {version}",
                                "dpkg": f"Audit Dpkg {version}",
                                "renovate": f"Audit Cleanup Renovate configuration for version {version}",
                                "trivy-filesystem": f"Audit {_TRIVY_FILESYSTEM_KEY} {version}",
                                "trivy-gradle-lock": f"Audit {_TRIVY_GRADLE_LOCK_KEY} {version}",
                            }[key_prefix]
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
                for key_prefix in ["Snyk check/fix", "Dpkg", _TRIVY_FILESYSTEM_KEY]:
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
                for output_prefix in ("snyk-", "trivy-images-", "trivy-published-", "trivy-filesystem-"):
                    if (
                        output.name.startswith(output_prefix)
                        and output.name[len(output_prefix) :] not in known_versions
                    ):
                        _LOGGER.debug("Deleting output %s", output.name)
                        await context.session.delete(output)
                        cleaned.append(f"output `{output.name}`")
                        break
            await context.session.commit()

            # Remove the Snyk projects of the references that are not supported anymore,
            # emptying a reference makes it disappear from the Snyk UI
            snyk_reports, snyk_success = await audit_utils.snyk_cleanup_removed_references(
                context.github_project.owner,
                context.github_project.repository,
                known_versions,
            )
            cleaned.extend(snyk_reports)

            # Remove the Snyk projects that were not re-monitored for too long,
            # independently of the monitor runs result
            stale_reports, stale_success = await audit_utils.snyk_cleanup_stale_projects_by_age(
                context.github_project.owner,
                context.github_project.repository,
            )
            cleaned.extend(stale_reports)
            snyk_success &= stale_success

            if not known_versions:
                # Clear all checks from dashboard
                issue_check.remove_check("outdated")
                issue_check.remove_check("snyk")
                issue_check.remove_check("dpkg")
                issue_check.remove_check("trivy-filesystem")

            logs_url = urllib.parse.urljoin(context.service_url, f"logs/{context.job_id}")
            summary_parts = []
            if cleaned:
                summary_parts.append(f"{len(cleaned)} leftover(s) removed")
            if not snyk_success:
                summary_parts.append("Snyk API error(s)")
            if summary_parts:
                summary = ", ".join(summary_parts)
                text_items = [*cleaned]
                if not snyk_success:
                    text_items.append("Snyk API error(s), see the logs")
                check_text: str | None = "\n".join(f"- {item}" for item in text_items)
            else:
                summary = "Everything is clean"
                check_text = None
            intermediate_status.status.types[_CLEANUP] = _TransversalStatusTool(
                name=_CLEANUP,
                summary=summary,
                status="success" if snyk_success else "error",
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

        filesystem_settings = context.module_config.get("trivy", {}).get("filesystem", {})
        if security_file is not None and filesystem_settings.get(
            "enabled", configuration.ENABLE_TRIVY_DEPENDENCIES_DEFAULT
        ):
            issue_check.add_check(
                "trivy-filesystem",
                "Check security vulnerabilities in the dependencies with Trivy",
                checked=False,
            )
            key_starts.append(f"{_TRIVY_FILESYSTEM_KEY} ")
        else:
            issue_check.remove_check("trivy-filesystem")

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

            success, renovate_messages = await _process_renovate(context, mapped_versions)
            return module.ProcessOutput(
                actions=actions,
                success=success,
                check_output={"summary": "\n".join(renovate_messages)} if renovate_messages else None,
            )
        if context.module_event_data.type == "outdated":
            await _process_outdated(context, issue_check)
        elif context.module_event_data.version is None:
            default_branch = await context.github_project.default_branch()

            # Determine whether the container images scan (Trivy) applies to this repository:
            # it is enabled by default but only relevant for repositories that have an `apps/`
            # directory of ArgoCD Helm applications.
            trivy_image_config = context.module_config.get("trivy", {}).get("image", {})
            trivy_enabled = trivy_image_config.get("enabled", configuration.ENABLE_TRIVY_DEFAULT)
            trivy_apps_root = trivy_image_config.get(
                "apps-root", configuration.APPLICATIONS_ROOT_DIRECTORY_DEFAULT
            )
            trivy_branches: list[str] = []
            if trivy_enabled:
                apps_present = False
                try:
                    await context.github_project.aio_github.rest.repos.async_get_content(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        path=trivy_apps_root,
                    )
                    apps_present = True
                except githubkit.exception.RequestFailed as exception:
                    if exception.response.status_code != 404:
                        raise
                if apps_present:
                    trivy_branches = [str(default_branch)]
                    issue_check.add_check(
                        "trivy",
                        "Scan the ArgoCD container images with Trivy",
                        checked=False,
                    )
            if not trivy_branches:
                issue_check.remove_check("trivy")

            # Determine whether the published container images scan (Trivy) applies: it needs the
            # Trivy image scan to be enabled and a `.github/publish.yaml` publishing configuration.
            trivy_published = False
            if trivy_enabled:
                publish_present = False
                try:
                    await context.github_project.aio_github.rest.repos.async_get_content(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        path=".github/publish.yaml",
                    )
                    publish_present = True
                except githubkit.exception.RequestFailed as exception:
                    if exception.response.status_code != 404:
                        raise
                if publish_present:
                    trivy_published = True
                    issue_check.add_check(
                        "trivy-published",
                        "Scan the published container images with Trivy",
                        checked=False,
                    )
            if not trivy_published:
                issue_check.remove_check("trivy-published")

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

            # Without a SECURITY.md there is no dependency audit to run; skip entirely unless
            # the Trivy container images scan applies to this repository.
            if not versions and not trivy_branches and not trivy_published:
                _LOGGER.debug(
                    "No SECURITY.md file in the repository and no ArgoCD applications, nothing to audit",
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
            all_key_starts.extend([f"{_TRIVY_KEY} {trivy_branch}" for trivy_branch in trivy_branches])
            if trivy_published:
                all_key_starts.append(f"{_TRIVY_PUBLISHED_KEY} {default_branch}")

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
                        known_versions=[*mapped_versions, default_branch],
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
                if context.module_event_data.trivy_filesystem and context.module_config.get("trivy", {}).get(
                    "filesystem", {}
                ).get(
                    "enabled",
                    configuration.ENABLE_TRIVY_DEPENDENCIES_DEFAULT,
                ):
                    actions.append(
                        module.Action(
                            priority=_PRIORITY_TRIVY_FILESYSTEM,
                            data=_EventData(type="trivy-filesystem", version=version),
                            title=f"trivy-filesystem ({version})",
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
            for trivy_branch in trivy_branches:
                actions.append(
                    module.Action(
                        priority=_PRIORITY_TRIVY,
                        data=_EventData(type="trivy", version=trivy_branch),
                        title=f"trivy ({trivy_branch})",
                    ),
                )
            if trivy_published:
                actions.append(
                    module.Action(
                        priority=_PRIORITY_TRIVY_PUBLISHED,
                        data=_EventData(
                            type="trivy-published",
                            version=str(default_branch),
                            versions=[*mapped_versions, str(default_branch)],
                        ),
                        title=f"trivy-published ({default_branch})",
                    ),
                )
            return ProcessOutput(
                actions=actions,
                dashboard=issue_check.to_string(),
                intermediate_status=intermediate_status,
                updated_transversal_status=True,
            )
        elif context.module_event_data.type == "trivy":
            short_message, success = await _process_trivy(
                context,
                issue_check,
                intermediate_status,
            )
        elif context.module_event_data.type == "trivy-published":
            short_message, success = await _process_trivy_published(
                context,
                issue_check,
                intermediate_status,
            )
        elif context.module_event_data.type == "trivy-filesystem":
            short_message, success = await _process_trivy_filesystem(
                context,
                issue_check,
                intermediate_status,
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
                "packages": "read",
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
        # The dashboard can be filtered to a single repository with `?repository=<owner>/<repo>`.
        selected_repository = context.params.get("repository")
        repositories = []
        for repository, data in context.status.repositories.items():
            if not data.types:
                continue
            if selected_repository is not None and repository != selected_repository:
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
                            "pull_request_url": type_data.pull_request_url,
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
                                "pull_request_url": type_data.pull_request_url,
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
