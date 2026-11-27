# Copyright (c) 2026, Camptocamp SA

"""Pull request secrets and misconfigurations scan with Trivy, part of the audit module."""

import asyncio
import json
import logging
import os
from typing import Any

import anyio
from pydantic import BaseModel

from github_app_geo_project import configuration
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration as audit_configuration
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)

_TRIVY_CACHE_DIR = "/var/www/.cache/trivy"
"""The Trivy cache directory, shared with the container images scans (keeps the databases warm)."""

_MAX_REPORTED_FINDINGS = 50
"""Maximum number of findings listed in the check summary, to keep the summary readable."""

# Don't run two pull request Trivy scans in parallel (the scan is long and resource hungry)
_TRIVY_PULL_REQUEST_LOCK = asyncio.Lock()


class Finding(BaseModel):
    """One finding reported by a Trivy scanner."""

    identifier: str
    severity: str
    title: str
    location: str


def _location(file: str, line: int | None) -> str:
    """Build the location string of a finding."""
    return f"{file}:{line}" if line is not None else file


def _secret_findings(result: dict[str, Any], target: str) -> list[Finding]:
    """Parse the secrets of one result of the Trivy JSON report."""
    findings: list[Finding] = []
    secrets = result.get("Secrets")
    if not isinstance(secrets, list):
        return findings
    for secret in secrets:
        if not isinstance(secret, dict):
            continue
        start_line = secret.get("StartLine")
        findings.append(
            Finding(
                identifier=str(secret.get("RuleID", "")),
                severity=str(secret.get("Severity", "")).lower(),
                title=str(secret.get("Title", "")),
                location=_location(target, start_line if isinstance(start_line, int) else None),
            ),
        )
    return findings


def _misconfiguration_findings(result: dict[str, Any], target: str) -> list[Finding]:
    """Parse the misconfigurations of one result of the Trivy JSON report."""
    findings: list[Finding] = []
    misconfigurations = result.get("Misconfigurations")
    if not isinstance(misconfigurations, list):
        return findings
    for misconfiguration in misconfigurations:
        if not isinstance(misconfiguration, dict):
            continue
        file = target
        line: int | None = None
        location = misconfiguration.get("Location")
        if isinstance(location, dict):
            if isinstance(location.get("File"), str):
                file = location["File"]
            start = location.get("Start")
            if isinstance(start, dict) and isinstance(start.get("Line"), int):
                line = start["Line"]
        findings.append(
            Finding(
                identifier=str(misconfiguration.get("AVDID", misconfiguration.get("ID", ""))),
                severity=str(misconfiguration.get("Severity", "")).lower(),
                title=str(misconfiguration.get("Title", "")),
                location=_location(file, line),
            ),
        )
    return findings


def parse_findings(stdout: str) -> list[Finding] | None:
    """
    Parse the findings of a ``trivy --format=json`` output.

    Returns ``None`` if the output is not a valid Trivy JSON report.
    """
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        _LOGGER.warning("Invalid Trivy JSON report")
        return None
    if not isinstance(report, dict):
        _LOGGER.warning("Invalid Trivy JSON report, no object at the root")
        return None
    results = report.get("Results")
    if not isinstance(results, list):
        return []
    findings: list[Finding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = str(result.get("Target", ""))
        findings.extend(_secret_findings(result, target))
        findings.extend(_misconfiguration_findings(result, target))
    return findings


def _report_lines(label: str, ok: bool, findings: list[Finding]) -> list[str]:
    """Build the summary lines of one Trivy command."""
    if not ok:
        return [f"**`{label}`**: :x: Trivy failed to run, see the job logs."]
    if not findings:
        return [f"**`{label}`**: :heavy_check_mark: No finding with the configured severities."]
    lines = [
        f"**`{label}`**: :warning: {len(findings)} finding(s) with the configured severities:",
    ]
    lines.extend(
        f"- `{finding.severity}` {finding.identifier} in `{finding.location}`: {finding.title}"
        for finding in findings[:_MAX_REPORTED_FINDINGS]
    )
    if len(findings) > _MAX_REPORTED_FINDINGS:
        lines.append(f"- ... and {len(findings) - _MAX_REPORTED_FINDINGS} more findings")
    return lines


async def _scan(
    command: list[str],
    env: dict[str, str],
    cwd: anyio.Path,
) -> tuple[bool, list[Finding]]:
    """Run one Trivy command and parse its findings, without failing on the findings."""
    stdout, success, _message = await module_utils.run_timeout(
        command,
        env,
        settings.audit.timeouts.trivy_pull_request,
        f"Trivy scan {' '.join(command[1:3])}",
        f"Trivy scan failed {' '.join(command[1:3])}",
        f"Trivy scan timeout {' '.join(command[1:3])}",
        cwd,
        error=False,
    )
    if not success or stdout is None:
        return False, []
    findings = parse_findings(stdout)
    if findings is None:
        return False, []
    return True, findings


async def scan(
    github_project: configuration.GithubProject,
    pull_request_number: int,
    config: audit_configuration.TrivyPullRequestConfiguration,
) -> tuple[bool, bool, str]:
    """
    Scan the head of the pull request with Trivy.

    Returns the success, whether the findings have to be reported as an
    ``action_required`` warning, and the summary of the check run.
    """
    scanners = config.get("scanners", audit_configuration.TRIVY_SCANNERS_DEFAULT)
    severity = config.get("severity", audit_configuration.TRIVY_SEVERITY_DEFAULT)
    ignore_file = config.get("ignore-file", audit_configuration.TRIVY_IGNORE_FILE_DEFAULT)
    config_targets = config.get("config-targets", audit_configuration.TRIVY_CONFIG_TARGETS_DEFAULT)

    await anyio.Path(_TRIVY_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "TRIVY_CACHE_DIR": _TRIVY_CACHE_DIR}

    messages: list[str] = []
    succeeded = True
    total_findings = 0

    async with (
        _TRIVY_PULL_REQUEST_LOCK,
        module_utils.GIT_WORKTREE_CACHE.pull_request_working_tree(
            github_project,
            pull_request_number,
        ) as worktree,
    ):
        arguments: list[str] = []
        if await (worktree / ignore_file).exists():
            arguments.append(f"--ignorefile={worktree / ignore_file}")
        arguments.append(f"--severity={severity}")

        scans: list[tuple[list[str], str]] = [
            (
                [
                    "trivy",
                    "fs",
                    f"--scanners={','.join(scanners)}",
                    *arguments,
                    "--format=json",
                    "--exit-code=0",
                    ".",
                ],
                "trivy fs",
            ),
        ]
        config_scans = 0
        for target in config_targets:
            if await (worktree / target).is_file():
                scans.append(
                    (
                        [
                            "trivy",
                            "config",
                            *arguments,
                            "--format=json",
                            "--exit-code=0",
                            target,
                        ],
                        f"trivy config {target}",
                    ),
                )
                config_scans += 1
        if config_targets and not config_scans:
            messages.append(
                f"**`trivy config`**: :heavy_check_mark: Skipped, "
                f"none of `{'`, `'.join(config_targets)}` exists in the repository.",
            )

        for command, label in scans:
            ok, findings = await _scan(command, env, worktree)
            succeeded = succeeded and ok
            total_findings += len(findings)
            messages.extend(_report_lines(label, ok, findings))

    if not succeeded:
        _LOGGER.warning("Trivy failed to scan the pull request #%i", pull_request_number)
    elif total_findings:
        _LOGGER.warning(
            "Trivy found %i findings on the pull request #%i",
            total_findings,
            pull_request_number,
        )

    return succeeded, succeeded and total_findings > 0, "\n".join(messages)
