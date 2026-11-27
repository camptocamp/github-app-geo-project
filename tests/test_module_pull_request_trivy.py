# Copyright (c) 2026, Camptocamp SA

"""Tests for the pull request Trivy scan module."""

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from github_app_geo_project import module
from github_app_geo_project.module.pull_request import trivy, trivy_configuration


def _make_worktree_mock(worktree_path: Path) -> MagicMock:
    """Create a mock for the pull request working tree async context manager."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(worktree_path))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


def _fs_report(findings: list[dict[str, Any]] | None) -> str:
    """Build a Trivy `fs` JSON report with the given secrets."""
    results = None
    if findings is not None:
        results = [{"Target": "config.py", "Scanner": "secret", "Secrets": findings}]
    return json.dumps({"SchemaVersion": 2, "Results": results})


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
    return json.dumps({"SchemaVersion": 2, "Results": results})


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


def test_parse_findings_secrets_and_misconfigurations() -> None:
    """`_parse_findings` maps the secrets and misconfigurations of the report."""
    stdout = json.dumps(
        {
            "SchemaVersion": 2,
            "Results": [
                {"Target": "config.py", "Secrets": [_SECRET_FINDING]},
                {"Target": "Dockerfile", "Misconfigurations": [_MISCONFIGURATION_FINDING]},
            ],
        },
    )
    findings = trivy._parse_findings(stdout)
    assert findings is not None
    assert [finding.identifier for finding in findings] == ["generic-api-key", "AVD-DS-0002"]
    assert [finding.location for finding in findings] == ["config.py:12", "Dockerfile:3"]
    assert [finding.severity for finding in findings] == ["critical", "high"]


def test_parse_findings_no_results() -> None:
    """`_parse_findings` returns an empty list when the report has no results."""
    assert trivy._parse_findings(_fs_report(None)) == []


def test_parse_findings_invalid_json() -> None:
    """`_parse_findings` returns None when the output is not a Trivy JSON report."""
    assert trivy._parse_findings("not json") is None


def test_get_actions_synchronize() -> None:
    """A `synchronize` event schedules one check job on the pull request."""
    trivy_module = trivy.Trivy()
    context = MagicMock(spec=module.GetActionContext)
    context.module_event_name = "pull_request"
    context.github_event_data = {}

    event_data = MagicMock()
    event_data.action = "synchronize"
    event_data.pull_request.number = 42

    with mock.patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = trivy_module.get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == trivy._EventData(pull_request_number=42)
    assert actions[0].checks is True
    assert actions[0].priority == module.PRIORITY_STATUS


def test_get_actions_other_action() -> None:
    """A `closed` event schedules nothing."""
    trivy_module = trivy.Trivy()
    context = MagicMock(spec=module.GetActionContext)
    context.module_event_name = "pull_request"
    context.github_event_data = {}

    event_data = MagicMock()
    event_data.action = "closed"

    with mock.patch("githubkit.webhooks.parse_obj", return_value=event_data):
        assert trivy_module.get_actions(context) == []


def _create_context() -> MagicMock:
    """Create a mock process context for the Trivy module."""
    context = MagicMock(spec=module.ProcessContext)
    context.module_config = trivy_configuration.PullRequestTrivyConfiguration()
    context.module_event_data = trivy._EventData(pull_request_number=42)
    context.github_project = MagicMock()
    return context


async def _run_process(
    worktree_path: Path,
    fs_stdout: str,
    fs_success: bool = True,
    config_stdout: str = _config_report(None),
) -> tuple[module.ProcessOutput, list[list[str]]]:
    """Run the module process with the working tree and Trivy commands mocked."""
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

    trivy_module = trivy.Trivy()
    context = _create_context()
    with (
        mock.patch.object(trivy, "_TRIVY_CACHE_DIR", str(worktree_path / "trivy-cache")),
        mock.patch(
            "github_app_geo_project.module.pull_request.trivy.module_utils.GIT_WORKTREE_CACHE"
            ".pull_request_working_tree",
            return_value=_make_worktree_mock(worktree_path),
        ),
        mock.patch(
            "github_app_geo_project.module.pull_request.trivy.module_utils.run_timeout",
            new=run_timeout,
        ),
    ):
        output = await trivy_module.process(context)
    return output, commands


@pytest.mark.asyncio
async def test_process_findings_action_required() -> None:
    """Findings are reported with the `action_required` conclusion, not a failure."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()
        (worktree_path / "Dockerfile").write_text("FROM scratch\n")
        github_dir = worktree_path / ".github"
        github_dir.mkdir()
        (github_dir / ".trivyignore.yaml").write_text("ignores: []\n")

        output, commands = await _run_process(
            worktree_path,
            _fs_report([_SECRET_FINDING]),
            config_stdout=_config_report([_MISCONFIGURATION_FINDING]),
        )

    assert output.success is True
    assert output.action_required is True
    summary = output.check_output["summary"] if output.check_output else ""
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
async def test_process_no_findings() -> None:
    """A clean scan is a success without any warning."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()
        (worktree_path / "Dockerfile").write_text("FROM scratch\n")

        output, commands = await _run_process(worktree_path, _fs_report([]))

    assert output.success is True
    assert output.action_required is False
    summary = output.check_output["summary"] if output.check_output else ""
    assert ":heavy_check_mark:" in summary
    assert len(commands) == 2


@pytest.mark.asyncio
async def test_process_without_ignore_file_or_dockerfile() -> None:
    """The `--ignorefile` argument and the `trivy config` command are only for existing files."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        output, commands = await _run_process(worktree_path, _fs_report([]))

    assert output.success is True
    assert len(commands) == 1
    assert commands[0][:2] == ["trivy", "fs"]
    assert not any(argument.startswith("--ignorefile=") for argument in commands[0])
    summary = output.check_output["summary"] if output.check_output else ""
    assert "Skipped" in summary


@pytest.mark.asyncio
async def test_process_trivy_tool_failure() -> None:
    """Trivy failing to run is reported as a failure, not as a warning."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        output, _commands = await _run_process(worktree_path, "", fs_success=False)

    assert output.success is False
    assert output.action_required is False
    summary = output.check_output["summary"] if output.check_output else ""
    assert ":x:" in summary


@pytest.mark.asyncio
async def test_process_invalid_report() -> None:
    """An unparsable Trivy report is reported as a failure."""
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        output, _commands = await _run_process(worktree_path, "not json")

    assert output.success is False
    assert output.action_required is False


@pytest.mark.asyncio
async def test_process_many_findings_truncated() -> None:
    """The summary lists at most the configured number of findings."""
    findings = [
        {**_SECRET_FINDING, "RuleID": f"rule-{index}", "StartLine": index}
        for index in range(trivy._MAX_REPORTED_FINDINGS + 10)
    ]
    with tempfile.TemporaryDirectory() as tmpdirname:
        worktree_path = Path(tmpdirname) / "repo"
        worktree_path.mkdir()

        output, _commands = await _run_process(worktree_path, _fs_report(findings))

    assert output.action_required is True
    summary = output.check_output["summary"] if output.check_output else ""
    assert f"{trivy._MAX_REPORTED_FINDINGS + 10} finding(s)" in summary
    assert "and 10 more findings" in summary
