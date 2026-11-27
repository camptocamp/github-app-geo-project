# Copyright (c) 2026, Camptocamp SA

"""Tests for the audit module pull request Trivy scan."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.audit import Audit, _EventData, pull_request

_FS_REPORT_TEMPLATE = {"SchemaVersion": 2, "Results": None}
_CONFIG_REPORT_TEMPLATE = {"SchemaVersion": 2, "Results": None}

_SECRET_FINDING = {
    "RuleID": "generic-api-key",
    "Severity": "CRITICAL",
    "Title": "Generic API Key",
    "StartLine": 12,
}

_MISCONFIGURATION_FINDING = {
    "ID": "DS002",
    "AVDID": "AVD-DS-0002",
    "Severity": "HIGH",
    "Title": "Image user should not be 'root'",
    "Location": {"File": "Dockerfile", "Start": {"Line": 3}},
}


def _fs_report(findings: list[dict[str, Any]] | None) -> str:
    """Build a Trivy `fs` JSON report with the given secrets."""
    results = None
    if findings is not None:
        results = [{"Target": "config.py", "Scanner": "secret", "Secrets": findings}]
    return json.dumps({**_FS_REPORT_TEMPLATE, "Results": results})


def _config_report(misconfigurations: list[dict[str, Any]] | None) -> str:
    """Build a Trivy `config` JSON report with the given misconfigurations."""
    results = None
    if misconfigurations is not None:
        results = [
            {
                "Target": "Dockerfile",
                "Scanner": "misconfig",
                "Misconfigurations": misconfigurations,
            },
        ]
    return json.dumps({**_CONFIG_REPORT_TEMPLATE, "Results": results})


def _make_worktree_mock(worktree_path: Path) -> MagicMock:
    """Create a mock for the pull request working tree async context manager."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(worktree_path))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


def test_parse_findings_secrets_and_misconfigurations() -> None:
    """`parse_findings` maps the secrets and misconfigurations of the report."""
    stdout = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {"Target": "config.py", "Secrets": [_SECRET_FINDING]},
                {"Target": "Dockerfile", "Misconfigurations": [_MISCONFIGURATION_FINDING]},
            ],
        },
    )
    findings = pull_request.parse_findings(stdout)
    assert findings is not None
    assert [finding.identifier for finding in findings] == ["generic-api-key", "AVD-DS-0002"]
    assert [finding.location for finding in findings] == ["config.py:12", "Dockerfile:3"]
    assert [finding.severity for finding in findings] == ["critical", "high"]


def test_parse_findings_no_results() -> None:
    """`parse_findings` returns an empty list when the report has no results."""
    assert pull_request.parse_findings(_fs_report(None)) == []


def test_parse_findings_invalid_json() -> None:
    """`parse_findings` returns None when the output is not a Trivy JSON report."""
    assert pull_request.parse_findings("not json") is None


async def _run_scan(
    worktree_path: Path,
    fs_stdout: str,
    fs_success: bool = True,
    config_stdout: str = _config_report(None),
) -> tuple[bool, bool, str, list[list[str]]]:
    """Run the scan with the working tree and the Trivy commands mocked."""
    commands: list[list[str]] = []

    async def run_timeout(
        command: list[str],
        env: dict[str, str] | None,
        timeout: Any,
        *args: Any,
        **kwargs: Any,
    ) -> tuple[str | None, bool, None]:
        del env, timeout, args, kwargs
        commands.append(command)
        if command[1] == "fs":
            return fs_stdout if fs_success else None, fs_success, None
        return config_stdout, True, None

    with (
        mock.patch.object(pull_request, "_TRIVY_CACHE_DIR", str(worktree_path / "trivy-cache")),
        mock.patch(
            "github_app_geo_project.module.audit.pull_request.module_utils.GIT_WORKTREE_CACHE"
            ".pull_request_working_tree",
            return_value=_make_worktree_mock(worktree_path),
        ),
        mock.patch(
            "github_app_geo_project.module.audit.pull_request.module_utils.run_timeout",
            new=run_timeout,
        ),
    ):
        succeeded, action_required, summary = await pull_request.scan(
            MagicMock(),
            42,
            {},
        )
    return succeeded, action_required, summary, commands


@pytest.mark.asyncio
async def test_scan_findings_action_required() -> None:
    """Findings are reported as an `action_required` warning, not a failure."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()
        (worktree_path / "Dockerfile").write_text("FROM scratch\n")
        github_dir = worktree_path / ".github"
        github_dir.mkdir()
        (github_dir / ".trivyignore.yaml").write_text("ignores: []\n")

        succeeded, action_required, summary, commands = await _run_scan(
            worktree_path,
            _fs_report([_SECRET_FINDING]),
            config_stdout=_config_report([_MISCONFIGURATION_FINDING]),
        )

    assert succeeded is True
    assert action_required is True
    assert ":warning:" in summary
    assert "generic-api-key in `config.py:12`" in summary
    assert "AVD-DS-0002 in `Dockerfile:3`" in summary

    assert commands[0][:2] == ["trivy", "fs"]
    assert "--scanners=secret,misconfig" in commands[0]
    assert "--severity=HIGH,CRITICAL" in commands[0]
    assert f"--ignorefile={worktree_path / '.github/.trivyignore.yaml'}" in commands[0]
    assert "--exit-code=0" in commands[0]
    assert commands[0][-1] == "."
    assert commands[1][:2] == ["trivy", "config"]
    assert commands[1][-1] == "Dockerfile"


@pytest.mark.asyncio
async def test_scan_no_findings() -> None:
    """A clean scan is a success without any warning."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()
        (worktree_path / "Dockerfile").write_text("FROM scratch\n")

        succeeded, action_required, summary, commands = await _run_scan(worktree_path, _fs_report([]))

    assert succeeded is True
    assert action_required is False
    assert ":heavy_check_mark:" in summary
    assert len(commands) == 2


@pytest.mark.asyncio
async def test_scan_without_ignore_file_or_dockerfile() -> None:
    """The `--ignorefile` argument and the `trivy config` command are only for existing files."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        succeeded, _action_required, summary, commands = await _run_scan(worktree_path, _fs_report([]))

    assert succeeded is True
    assert len(commands) == 1
    assert commands[0][:2] == ["trivy", "fs"]
    assert not any(argument.startswith("--ignorefile=") for argument in commands[0])
    assert "Skipped" in summary


@pytest.mark.asyncio
async def test_scan_trivy_tool_failure() -> None:
    """Trivy failing to run is reported as a failure, not as a warning."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        succeeded, action_required, summary, _commands = await _run_scan(worktree_path, "", fs_success=False)

    assert succeeded is False
    assert action_required is False
    assert ":x:" in summary


@pytest.mark.asyncio
async def test_scan_invalid_report() -> None:
    """An unparsable Trivy report is reported as a failure."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        succeeded, action_required, _summary, _commands = await _run_scan(worktree_path, "not json")

    assert succeeded is False
    assert action_required is False


@pytest.mark.asyncio
async def test_scan_many_findings_truncated() -> None:
    """The summary lists at most the configured number of findings."""
    findings = [
        {**_SECRET_FINDING, "RuleID": f"rule-{index}", "StartLine": index}
        for index in range(pull_request._MAX_REPORTED_FINDINGS + 10)
    ]
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        _succeeded, action_required, summary, _commands = await _run_scan(worktree_path, _fs_report(findings))

    assert action_required is True
    assert f"{pull_request._MAX_REPORTED_FINDINGS + 10} finding(s)" in summary
    assert "and 10 more findings" in summary


def _pull_request_event(action: str, number: int = 42) -> MagicMock:
    """Create a mock parsed pull request event."""
    event_data = MagicMock()
    event_data.action = action
    event_data.pull_request.number = number
    return event_data


def test_get_actions_synchronize() -> None:
    """A `synchronize` event schedules one Trivy scan job on the pull request."""
    audit = Audit()
    context = MagicMock(spec=module.GetActionContext)
    context.module_event_name = "pull_request"
    context.github_event_data = {}

    with mock.patch("githubkit.webhooks.parse_obj", return_value=_pull_request_event("synchronize")):
        actions = audit.get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="trivy-pull-request")
    assert actions[0].title == "trivy-pull-request (42)"
    assert actions[0].priority == module.PRIORITY_STATUS


def test_get_actions_closed() -> None:
    """A `closed` event still schedules the issues cleanup job."""
    audit = Audit()
    context = MagicMock(spec=module.GetActionContext)
    context.module_event_name = "pull_request"
    context.github_event_data = {}

    with mock.patch("githubkit.webhooks.parse_obj", return_value=_pull_request_event("closed")):
        actions = audit.get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="close-pull-request-issues")


def _process_context(config: dict[str, Any]) -> MagicMock:
    """Create a mock process context of a Trivy pull request job."""
    context = MagicMock(spec=module.ProcessContext)
    context.module_event_name = "pull_request"
    context.github_event_name = "pull_request"
    context.github_event_data = {"action": "synchronize"}
    context.module_event_data = _EventData(type="trivy-pull-request")
    context.module_config = config
    context.issue_data = ""
    context.service_url = "https://example.com/"
    context.job_id = 1
    context.github_project = MagicMock()
    return context


@pytest.mark.asyncio
async def test_process_trivy_pull_request_disabled() -> None:
    """A disabled scan succeeds without running Trivy."""
    audit = Audit()
    context = _process_context({"trivy": {"pull-request": {"enabled": False}}})

    with mock.patch(
        "github_app_geo_project.module.audit.audit_pull_request.scan",
        new=AsyncMock(),
    ) as scan_mock:
        output = await audit.process(context)

    scan_mock.assert_not_awaited()
    assert output.success is True
    assert output.action_required is False
    assert "disabled" in (output.check_output or {})["summary"]


@pytest.mark.asyncio
async def test_process_trivy_pull_request_findings() -> None:
    """Findings are mapped to the `action_required` conclusion of the module output."""
    audit = Audit()
    context = _process_context({})

    with (
        mock.patch(
            "github_app_geo_project.module.audit.audit_pull_request.scan",
            new=AsyncMock(return_value=(True, True, ":warning: 1 finding")),
        ),
        mock.patch("githubkit.webhooks.parse_obj", return_value=_pull_request_event("synchronize")),
    ):
        output = await audit.process(context)

    assert output.success is True
    assert output.action_required is True
    assert output.check_output == {"summary": ":warning: 1 finding"}


@pytest.mark.asyncio
async def test_process_trivy_pull_request_tool_failure() -> None:
    """A Trivy tool error fails the check run."""
    audit = Audit()
    context = _process_context({})

    with (
        mock.patch(
            "github_app_geo_project.module.audit.audit_pull_request.scan",
            new=AsyncMock(return_value=(False, False, ":x: Trivy failed to run, see the job logs.")),
        ),
        mock.patch("githubkit.webhooks.parse_obj", return_value=_pull_request_event("synchronize")),
    ):
        output = await audit.process(context)

    assert output.success is False
    assert output.action_required is False
