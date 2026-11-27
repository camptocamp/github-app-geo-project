# Copyright (c) 2026, Camptocamp SA

"""Module to scan the pull request code with Trivy."""

import json
import logging
import os
from pathlib import Path
from typing import Any

import anyio
import githubkit.webhooks
from pydantic import BaseModel, Field

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.pull_request import trivy_configuration
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)

_TRIVY_CACHE_DIR = "/var/www/.cache/trivy"
"""The Trivy cache directory, shared with the audit container images scans in the worker pods."""

_MAX_REPORTED_FINDINGS = 50
"""Maximum number of findings listed in the check summary, to keep the summary readable."""


class _EventData(BaseModel):
    """The data of a Trivy pull request scan job."""

    pull_request_number: int = Field(description="The number of the pull request to scan")


class _Finding(BaseModel):
    """One finding reported by a Trivy scanner."""

    identifier: str
    severity: str
    title: str
    location: str


def _location(file: str, line: int | None) -> str:
    """Build the location string of a finding."""
    return f"{file}:{line}" if line is not None else file


def _secret_findings(result: dict[str, Any], target: str) -> list[_Finding]:
    """Parse the secrets of one result of the Trivy JSON report."""
    findings: list[_Finding] = []
    secrets = result.get("Secrets")
    if not isinstance(secrets, list):
        return findings
    for secret in secrets:
        if not isinstance(secret, dict):
            continue
        start_line = secret.get("StartLine")
        findings.append(
            _Finding(
                identifier=str(secret.get("RuleID", "")),
                severity=str(secret.get("Severity", "")).lower(),
                title=str(secret.get("Title", "")),
                location=_location(target, start_line if isinstance(start_line, int) else None),
            ),
        )
    return findings


def _misconfiguration_findings(result: dict[str, Any], target: str) -> list[_Finding]:
    """Parse the misconfigurations of one result of the Trivy JSON report."""
    findings: list[_Finding] = []
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
            _Finding(
                identifier=str(misconfiguration.get("AVDID", misconfiguration.get("ID", ""))),
                severity=str(misconfiguration.get("Severity", "")).lower(),
                title=str(misconfiguration.get("Title", "")),
                location=_location(file, line),
            ),
        )
    return findings


def _parse_findings(stdout: str) -> list[_Finding] | None:
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
    findings: list[_Finding] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        target = str(result.get("Target", ""))
        findings.extend(_secret_findings(result, target))
        findings.extend(_misconfiguration_findings(result, target))
    return findings


def _report_lines(label: str, ok: bool, findings: list[_Finding]) -> list[str]:
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
) -> tuple[bool, list[_Finding]]:
    """Run one Trivy command and parse its findings, without failing on the findings."""
    stdout, success, _message = await module_utils.run_timeout(
        command,
        env,
        settings.pull_request.timeouts.trivy_scan,
        f"Trivy scan {' '.join(command[1:3])}",
        f"Trivy scan failed {' '.join(command[1:3])}",
        f"Trivy scan timeout {' '.join(command[1:3])}",
        cwd,
        error=False,
    )
    if not success or stdout is None:
        return False, []
    findings = _parse_findings(stdout)
    if findings is None:
        return False, []
    return True, findings


class Trivy(
    module.Module[
        trivy_configuration.PullRequestTrivyConfiguration,
        _EventData,
        None,
        None,
    ],
):
    """Module that scans the pull request code with Trivy."""

    def title(self) -> str:
        """Get the title."""
        return "Trivy"

    def description(self) -> str:
        """Get the description."""
        return "Scan the pull request code with Trivy, reported as a warning check"

    def documentation_url(self) -> str:
        """Get the documentation URL."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/pull_request/Trivy.md"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "read",
            },
            {"pull_request"},
        )

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        with (Path(__file__).parent / "trivy-schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def jobs_unique_on(self) -> list[module.Fields] | None:
        """Skip the queued scans replaced by a newer scan of the same pull request."""
        return [
            module.Fields.OWNER,
            module.Fields.REPOSITORY,
            module.Fields.MODULE_EVENT_NAME,
            module.Fields.MODULE_EVENT_DATA,
        ]

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_EventData]]:
        """Get the actions to execute."""
        if context.module_event_name == "pull_request":
            event_data = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            if event_data.action in ("opened", "reopened", "synchronize"):
                return [
                    module.Action(
                        _EventData(pull_request_number=event_data.pull_request.number),
                        checks=True,
                        priority=module.PRIORITY_STATUS,
                    ),
                ]
        return []

    async def process(
        self,
        context: module.ProcessContext[
            trivy_configuration.PullRequestTrivyConfiguration,
            _EventData,
        ],
    ) -> module.ProcessOutput[_EventData, None]:
        """Process the module."""
        config = context.module_config
        scanners = config.get("scanners", trivy_configuration.PULL_REQUEST_TRIVY_SCANNERS_DEFAULT)
        severity = config.get("severity", trivy_configuration.PULL_REQUEST_TRIVY_SEVERITY_DEFAULT)
        ignore_file = config.get("ignore-file", trivy_configuration.PULL_REQUEST_TRIVY_IGNORE_FILE_DEFAULT)
        config_targets = config.get(
            "config-targets", trivy_configuration.PULL_REQUEST_TRIVY_CONFIG_TARGETS_DEFAULT
        )

        await anyio.Path(_TRIVY_CACHE_DIR).mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "TRIVY_CACHE_DIR": _TRIVY_CACHE_DIR}

        messages: list[str] = []
        succeeded = True
        total_findings = 0

        async with module_utils.GIT_WORKTREE_CACHE.pull_request_working_tree(
            context.github_project,
            context.module_event_data.pull_request_number,
        ) as worktree:
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
            _LOGGER.warning(
                "Trivy failed to scan the pull request #%i", context.module_event_data.pull_request_number
            )
        elif total_findings:
            _LOGGER.warning(
                "Trivy found %i findings on the pull request #%i",
                total_findings,
                context.module_event_data.pull_request_number,
            )

        return module.ProcessOutput(
            success=succeeded,
            action_required=succeeded and total_findings > 0,
            check_output={"summary": "\n".join(messages)},
        )
