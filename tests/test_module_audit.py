# Copyright (c) 2026, Camptocamp SA

"""Tests for the audit module."""

import asyncio
import base64
import datetime as dt
import html as html_module
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Self
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import anyio
import githubkit.exception
import githubkit_schemas.latest.models
import pytest
from multi_repo_automation.editor import JSON5List

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import (
    _PRIORITY_TRIVY_FILESYSTEM,
    Audit,
    _create_pull_request_if_changes,
    _dashboard_vuln_section_versions,
    _details_markdown,
    _EventData,
    _fixed_vulnerabilities_markdown,
    _IntermediateStatus,
    _OutputRendererData,
    _pinned_vulnerabilities_markdown,
    _process_renovate,
    _process_snyk_dpkg,
    _process_trivy,
    _process_trivy_filesystem,
    _remove_dashboard_vuln_section,
    _TransversalStatus,
    _TransversalStatusRepo,
    _TransversalStatusTool,
    _vulnerability_status,
    _VulnerabilityStatus,
)
from github_app_geo_project.module.audit import utils as audit_utils
from github_app_geo_project.module.audit.utils import VulnerabilityData
from github_app_geo_project.settings import settings
from github_app_geo_project.templates import render_template


def _make_worktree_mock(clone_path: Path) -> MagicMock:
    """Create a mock for GIT_WORKTREE_CACHE.working_tree async context manager."""
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=anyio.Path(clone_path))
    mock_cm.__aexit__ = AsyncMock(return_value=None)
    return mock_cm


@pytest.mark.asyncio
async def test_process_renovate_default_branch_success():
    """Test successful Renovate update on default branch."""
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123

    known_versions = ["1.0", "2.0"]
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{\n}")

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit.editor.EditRenovateConfig") as mock_editor,
        ):
            mock_config = MagicMock()
            mock_editor.return_value = MagicMock(
                __aenter__=AsyncMock(return_value=mock_config),
                __aexit__=AsyncMock(return_value=None),
            )

            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (True, [], None)

                result = await _process_renovate(context, known_versions)

                assert result == (True, [])
                assert mock_cm.__aenter__.called

                mock_config.__setitem__.assert_called_once_with(
                    "baseBranchPatterns", ["master", "1.0", "2.0"]
                )

                mock_create_pr.assert_called_once()
                pr_call_args = mock_create_pr.call_args
                assert pr_call_args[0][0] == "master"
                assert pr_call_args[0][1] == "ghci/audit/renovate/master"
                assert pr_call_args[0][2] == "Update Renovate configuration"


@pytest.mark.asyncio
async def test_process_renovate_default_branch_no_security_file():
    """Test Renovate update when SECURITY.md is missing."""
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123

    known_versions = []
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{\n}")

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit.editor.EditRenovateConfig") as mock_editor,
        ):
            mock_config = MagicMock()
            mock_config.__contains__.return_value = True
            mock_editor.return_value = MagicMock(
                __aenter__=AsyncMock(return_value=mock_config),
                __aexit__=AsyncMock(return_value=None),
            )

            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (True, [], None)

                result = await _process_renovate(context, known_versions)

                assert result == (True, [])
                mock_config.__setitem__.assert_not_called()
                mock_config.__delitem__.assert_called_once_with("baseBranchPatterns")
                mock_create_pr.assert_called_once()


@pytest.mark.asyncio
async def test_process_renovate_default_branch_up_to_date():
    """The Renovate config is not rewritten when the baseBranchPatterns are already up to date."""
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123

    known_versions = ["1.0", "2.0"]
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{\n}")

        current_patterns = JSON5List()
        for pattern in ["master", "1.0", "2.0"]:
            current_patterns.append(pattern)

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit.editor.EditRenovateConfig") as mock_editor,
        ):
            mock_config = MagicMock()
            mock_config.get.return_value = current_patterns
            mock_editor.return_value = MagicMock(
                __aenter__=AsyncMock(return_value=mock_config),
                __aexit__=AsyncMock(return_value=None),
            )

            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (True, [], None)

                result = await _process_renovate(context, known_versions)

                assert result == (True, [])
                mock_config.__setitem__.assert_not_called()
                mock_config.__delitem__.assert_not_called()
                mock_create_pr.assert_called_once()


@pytest.mark.asyncio
async def test_process_renovate_default_branch_clone_failure():
    """Test failed worktree creation on default branch scenario."""
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")

    known_versions = ["1.0", "2.0"]
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=ValueError("Failed to update branch master"))
    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
            return_value=mock_cm,
        ),
        pytest.raises(ValueError, match="Failed to update branch master"),
    ):
        await _process_renovate(context, known_versions)


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_success():
    """Test successful version cleanup scenario."""
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{}")
        security_file = clone_path / "SECURITY.md"
        security_file.write_text("# Security")

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
        ):
            mock_create_pr.return_value = (True, [], None)

            result = await _process_renovate(context, None)

            assert result == (True, [])
            assert not renovate_file.exists()
            assert not security_file.exists()

            mock_create_pr.assert_called_once()
            pr_call_args = mock_create_pr.call_args
            assert pr_call_args[0][0] == "1.0"
            assert pr_call_args[0][1] == "ghci/audit/renovate/1.0"


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_clone_failure():
    """Test failed worktree creation on version cleanup scenario."""
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(side_effect=ValueError("Failed to update branch 1.0"))
    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
            return_value=mock_cm,
        ),
        pytest.raises(ValueError, match="Failed to update branch 1\\.0"),
    ):
        await _process_renovate(context, None)


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_files_not_exist():
    """Test version cleanup when files don't exist."""
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
        ):
            mock_create_pr.return_value = (True, [], None)

            result = await _process_renovate(context, None)

            assert result == (True, [])
            mock_create_pr.assert_called_once()


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_pr_creation_failure():
    """Test version cleanup when PR creation fails."""
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()

        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
        ):
            mock_create_pr.return_value = (False, ["Error creating PR"], None)

            result = await _process_renovate(context, None)

            assert result == (False, ["Error creating PR"])


def _make_create_pull_request_if_changes_context() -> Mock:
    context = Mock()
    context.module_event_data = _EventData(type="dpkg", version="4.0")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "mapfish-print"
    context.service_url = "https://example.com/"
    context.job_id = 123
    return context


@pytest.mark.asyncio
async def test_create_pull_request_if_changes_stale_pull_request() -> None:
    """A pull request open for too long reports the reason instead of the generic error."""
    context = _make_create_pull_request_if_changes_context()

    pull_request = MagicMock()
    pull_request.html_url = "https://github.com/camptocamp/mapfish-print/pull/4438"
    stale_message = (
        "Pull request [#4438](https://github.com/camptocamp/mapfish-print/pull/4438) is open for 7 days"
    )

    diff_proc = MagicMock()
    diff_proc.returncode = 1
    diff_proc.communicate = AsyncMock(return_value=(b"", b""))

    issue_check = module_utils.DashboardIssue("- [ ] <!-- Dpkg 4.0 --> Update dpkg packages\n")

    with (
        tempfile.TemporaryDirectory() as tmpdirname,
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=diff_proc)),
        patch(
            "github_app_geo_project.module.audit.module_utils.run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ),
        patch.object(audit_utils, "get_pre_commit_config", return_value={}),
        patch.object(
            module_utils,
            "create_commit_pull_request",
            new=AsyncMock(
                return_value=module_utils.PullRequestResult(
                    module_utils.PullRequestStatus.STALE,
                    pull_request,
                    stale_message,
                )
            ),
        ),
        patch("github_app_geo_project.module.audit._LOGGER") as mock_logger,
    ):
        success, short_message, pull_request_url = await _create_pull_request_if_changes(
            "4.0",
            "ghci/audit/dpkg/4.0",
            "Dpkg 4.0",
            "Update dpkg packages",
            context,
            {},
            anyio.Path(tmpdirname),
            issue_check,
        )

    assert success is False
    assert short_message == [stale_message]
    assert pull_request_url == pull_request.html_url
    assert issue_check.get_title("Dpkg 4.0") == f"Dpkg 4.0: {stale_message}"
    mock_logger.error.assert_not_called()


@pytest.mark.asyncio
async def test_create_pull_request_if_changes_error() -> None:
    """A real commit or push failure keeps logging the generic error and reports no message."""
    context = _make_create_pull_request_if_changes_context()

    diff_proc = MagicMock()
    diff_proc.returncode = 1
    diff_proc.communicate = AsyncMock(return_value=(b"", b""))

    issue_check = module_utils.DashboardIssue("- [ ] <!-- Dpkg 4.0 --> Update dpkg packages\n")

    with (
        tempfile.TemporaryDirectory() as tmpdirname,
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=diff_proc)),
        patch(
            "github_app_geo_project.module.audit.module_utils.run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ),
        patch.object(audit_utils, "get_pre_commit_config", return_value={}),
        patch.object(
            module_utils,
            "create_commit_pull_request",
            new=AsyncMock(return_value=module_utils.PullRequestResult(module_utils.PullRequestStatus.ERROR)),
        ),
        patch("github_app_geo_project.module.audit._LOGGER") as mock_logger,
    ):
        success, short_message, pull_request_url = await _create_pull_request_if_changes(
            "4.0",
            "ghci/audit/dpkg/4.0",
            "Dpkg 4.0",
            "Update dpkg packages",
            context,
            {},
            anyio.Path(tmpdirname),
            issue_check,
        )

    assert success is False
    assert short_message == []
    assert pull_request_url is None
    mock_logger.error.assert_called_once_with("Error while create commit or pull request")


@pytest.mark.asyncio
async def test_create_pull_request_if_changes_no_changes() -> None:
    """The NO_CHANGES status is a success and closes the pull requests of the previous runs."""
    context = _make_create_pull_request_if_changes_context()

    diff_proc = MagicMock()
    diff_proc.returncode = 1
    diff_proc.communicate = AsyncMock(return_value=(b"", b""))

    issue_check = module_utils.DashboardIssue("- [ ] <!-- Dpkg 4.0 --> Update dpkg packages\n")

    with (
        tempfile.TemporaryDirectory() as tmpdirname,
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=diff_proc)),
        patch(
            "github_app_geo_project.module.audit.module_utils.run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ),
        patch.object(audit_utils, "get_pre_commit_config", return_value={}),
        patch.object(
            module_utils,
            "create_commit_pull_request",
            new=AsyncMock(
                return_value=module_utils.PullRequestResult(module_utils.PullRequestStatus.NO_CHANGES)
            ),
        ),
        patch.object(module_utils, "close_pull_request_issues", new=AsyncMock()) as mock_close,
    ):
        success, short_message, pull_request_url = await _create_pull_request_if_changes(
            "4.0",
            "ghci/audit/dpkg/4.0",
            "Dpkg 4.0",
            "Update dpkg packages",
            context,
            {},
            anyio.Path(tmpdirname),
            issue_check,
        )

    assert success is True
    assert short_message == []
    assert pull_request_url is None
    mock_close.assert_awaited_once_with(
        "ghci/audit/dpkg/4.0",
        "Audit Dpkg 4.0",
        context.github_project,
    )


def test_get_actions_pull_request_closed() -> None:
    """Test that a closed pull request triggers issue closing action."""
    context = Mock()
    context.module_event_name = "pull_request"
    context.github_event_data = {
        "action": "closed",
        "repository": {"default_branch": "master"},
    }

    event_data = Mock()
    event_data.action = "closed"
    event_data.pull_request = Mock()
    event_data.pull_request.number = 1234
    event_data.pull_request.merged = False
    event_data.pull_request.base = Mock()
    event_data.pull_request.base.ref = "master"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="close-pull-request-issues")
    assert actions[0].title == "close-pull-request-issues (1234)"


def test_jobs_unique_on() -> None:
    """Test that the audit jobs are unique per owner, repository and event name."""
    assert Audit().jobs_unique_on() == [
        module.Fields.OWNER,
        module.Fields.REPOSITORY,
        module.Fields.MODULE_EVENT_NAME,
    ]


def test_get_actions_pull_request_closed_merged_default_branch_triggers_renovate() -> None:
    """Test that merged pull request on default branch only closes related issues."""
    context = Mock()
    context.module_event_name = "pull_request"
    context.github_event_data = {
        "action": "closed",
        "repository": {"default_branch": "master"},
    }

    event_data = Mock()
    event_data.action = "closed"
    event_data.pull_request = Mock()
    event_data.pull_request.merged = True
    event_data.pull_request.base = Mock()
    event_data.pull_request.base.ref = "master"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="close-pull-request-issues")


def test_get_actions_pull_request_closed_merged_non_default_branch_no_renovate() -> None:
    """Test that merged pull request on non-default branch does not trigger Renovate."""
    context = Mock()
    context.module_event_name = "pull_request"
    context.github_event_data = {
        "action": "closed",
        "repository": {"default_branch": "master"},
    }

    event_data = Mock()
    event_data.action = "closed"
    event_data.pull_request = Mock()
    event_data.pull_request.merged = True
    event_data.pull_request.base = Mock()
    event_data.pull_request.base.ref = "4.0.0"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="close-pull-request-issues")


@pytest.mark.asyncio
async def test_process_close_pull_request_issues_action() -> None:
    """Test processing close-pull-request-issues event data."""
    context = Mock()
    context.module_event_data = _EventData(type="close-pull-request-issues")
    context.github_event_data = {"action": "closed"}
    context.github_project = Mock()
    context.issue_data = ""

    event_data = Mock()
    event_data.pull_request = Mock()
    event_data.pull_request.number = 42
    event_data.pull_request.title = "Audit Snyk check/fix prod-2-9-advance"

    with (
        patch("githubkit.webhooks.parse_obj", return_value=event_data),
        patch(
            "github_app_geo_project.module.audit.module_utils.close_pull_request_related_issues",
            new=AsyncMock(),
        ) as mock_close_related,
    ):
        result = await Audit().process(context)

    mock_close_related.assert_awaited_once_with(context.github_project, 42, event_data.pull_request.title)
    assert result.success is True


def test_get_actions_push_security_md_on_default_branch_triggers_renovate() -> None:
    """Test that SECURITY.md change on default branch triggers outdated and renovate."""
    context = Mock()
    context.module_event_name = "push"
    context.github_event_data = {"ref": "refs/heads/master"}

    event_data = Mock()
    event_data.commits = [Mock(modified=["SECURITY.md"], added=[], removed=[])]
    event_data.ref = "refs/heads/master"
    event_data.repository = Mock()
    event_data.repository.default_branch = "master"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 2
    assert actions[0].data == _EventData(type="outdated")
    assert actions[1].data == _EventData(type="renovate")


def test_get_actions_push_security_md_on_non_default_branch_no_renovate() -> None:
    """Test that SECURITY.md change on non-default branch does not trigger renovate."""
    context = Mock()
    context.module_event_name = "push"
    context.github_event_data = {"ref": "refs/heads/4.0.0"}

    event_data = Mock()
    event_data.commits = [Mock(modified=["SECURITY.md"], added=[], removed=[])]
    event_data.ref = "refs/heads/4.0.0"
    event_data.repository = Mock()
    event_data.repository.default_branch = "master"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="outdated")


def test_vulnerability_data_structure() -> None:
    """Test VulnerabilityData creation."""

    vuln = VulnerabilityData(
        file="requirements.txt",
        package_name="django",
        package_version="3.2.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-DJANGO-123456",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-79"],
        title="[HIGH] django@3.2.0: [CVE-2024-12345]",
        fixed_in=["3.2.1"],
        is_upgradable=True,
        is_patchable=False,
    )
    assert vuln.file == "requirements.txt"
    assert vuln.severity == "high"
    assert vuln.cve_ids == ["CVE-2024-12345"]
    assert vuln.cwe_ids == ["CWE-79"]


def test_severity_order() -> None:
    """Test severity ordering."""
    from github_app_geo_project.module.audit.utils import SEVERITY_ORDER

    assert SEVERITY_ORDER["low"] < SEVERITY_ORDER["medium"]
    assert SEVERITY_ORDER["medium"] < SEVERITY_ORDER["high"]
    assert SEVERITY_ORDER["high"] < SEVERITY_ORDER["critical"]


def test_ecosystem_map() -> None:
    """Test GitHub ecosystem mapping."""
    from github_app_geo_project.module.audit.utils import ECOSYSTEM_MAP

    assert ECOSYSTEM_MAP["pip"] == "pip"
    assert ECOSYSTEM_MAP["npm"] == "npm"
    assert ECOSYSTEM_MAP["gomodules"] == "go"
    assert ECOSYSTEM_MAP["cargo"] == "rust"
    assert ECOSYSTEM_MAP.get("unknown", "other") == "other"


def test_get_severity_config() -> None:
    """Test severity config retrieval with fallback."""
    from github_app_geo_project.module.audit.utils import get_severity_config

    config: dict = {}
    local_config: dict = {}

    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "medium"

    config["dashboard-severity-threshold"] = "high"
    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "high"

    local_config["dashboard-severity-threshold"] = "critical"
    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "critical"


def test_get_excluded_files() -> None:
    """Test excluded files config retrieval."""
    from github_app_geo_project.module.audit.utils import get_excluded_files

    config: dict = {}
    local_config: dict = {}

    result = get_excluded_files(config, local_config)
    assert result == []

    config["excluded-files"] = [r"dev-.*\.txt"]
    result = get_excluded_files(config, local_config)
    assert result == [r"dev-.*\.txt"]

    local_config["excluded-files"] = [r"test-.*\.txt"]
    result = get_excluded_files(config, local_config)
    assert result == [r"test-.*\.txt"]


def test_vulnerability_deduplication() -> None:
    """Test that VulnerabilityData with same (snyk_id, package_version) for the same file is deduplicated."""

    vuln1 = VulnerabilityData(
        file="pyproject.toml",
        package_name="black",
        package_version="24.3.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-BLACK-15518063",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-22"],
        title="[HIGH] black@24.3.0: [SNYK-PYTHON-BLACK-15518063]",
        fixed_in=["26.3.1"],
        is_upgradable=True,
        is_patchable=False,
    )
    vuln2 = VulnerabilityData(
        file="pyproject.toml",
        package_name="black",
        package_version="24.3.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-BLACK-15518063",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-22"],
        title="[HIGH] black@24.3.0: [SNYK-PYTHON-BLACK-15518063]",
        fixed_in=["26.3.1"],
        is_upgradable=True,
        is_patchable=False,
    )
    vuln3 = VulnerabilityData(
        file="pyproject.toml",
        package_name="black",
        package_version="24.3.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-BLACK-15518063",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-22"],
        title="[HIGH] black@24.3.0: [SNYK-PYTHON-BLACK-15518063]",
        fixed_in=["26.3.1"],
        is_upgradable=True,
        is_patchable=False,
    )

    file_vulnerabilities: dict[str, list[VulnerabilityData]] = {}
    for vuln in [vuln1, vuln2, vuln3]:
        existing = file_vulnerabilities.setdefault(vuln.file, [])
        if not any(v.snyk_id == vuln.snyk_id and v.package_version == vuln.package_version for v in existing):
            existing.append(vuln)

    assert len(file_vulnerabilities["pyproject.toml"]) == 1
    assert file_vulnerabilities["pyproject.toml"][0].snyk_id == "SNYK-PYTHON-BLACK-15518063"


def test_vulnerability_deduplication_different_versions() -> None:
    """Test that different versions of the same vulnerability are not considered duplicates."""

    vuln1 = VulnerabilityData(
        file="pyproject.toml",
        package_name="black",
        package_version="24.3.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-BLACK-15518063",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-22"],
        title="[HIGH] black@24.3.0: [SNYK-PYTHON-BLACK-15518063]",
        fixed_in=["26.3.1"],
        is_upgradable=True,
        is_patchable=False,
    )
    vuln2 = VulnerabilityData(
        file="pyproject.toml",
        package_name="black",
        package_version="26.3.1",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-BLACK-15518063",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-22"],
        title="[HIGH] black@26.3.1: [SNYK-PYTHON-BLACK-15518063]",
        fixed_in=["26.3.1"],
        is_upgradable=True,
        is_patchable=False,
    )

    file_vulnerabilities: dict[str, list[VulnerabilityData]] = {}
    for vuln in [vuln1, vuln2]:
        existing = file_vulnerabilities.setdefault(vuln.file, [])
        if not any(v.snyk_id == vuln.snyk_id and v.package_version == vuln.package_version for v in existing):
            existing.append(vuln)

    assert len(file_vulnerabilities["pyproject.toml"]) == 2


def test_issue_body_cleanup() -> None:
    """Test that non-action items are removed from the issue body."""
    from github_app_geo_project.module import utils as module_utils

    DashboardIssue = module_utils.DashboardIssue

    issue = DashboardIssue(
        "## Audit (Snyk/dpkg/Renovate)\n"
        "\n"
        "- [ ] <!-- outdated --> Check outdated version\n"
        "- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"
        "- [ ] <!-- dpkg --> Update dpkg packages\n"
        "\n"
        "==== pyproject.toml\n"
        "- [HIGH] black@24.3.0: ..\n"
        "==== requirements.txt\n"
        "- [HIGH] dulwich@0.21.7: ..\n"
    )
    issue.issue = [item for item in issue.issue if isinstance(item, module_utils.DashboardIssueItem)]
    result = issue.to_string()
    assert "==== pyproject.toml" not in result
    assert "==== requirements.txt" not in result
    assert "[HIGH]" not in result
    assert "Check outdated version" in result
    assert "Check security vulnerabilities with Snyk" in result
    assert "Update dpkg packages" in result


@pytest.mark.asyncio
async def test_process_archived_repository() -> None:
    from github_app_geo_project.module.audit import Audit

    audit = Audit()
    context = Mock()
    context.module_event_data = _EventData(type="outdated")
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "archived-repo"
    context.module_config = {}
    context.github_event_data = {"type": "event", "name": "daily"}

    github = MagicMock()
    context.github_project.aio_github = github
    rest = MagicMock()
    github.rest = rest
    repos = AsyncMock()
    rest.repos = repos
    response = MagicMock()
    response.parsed_data.archived = True
    repos.async_get.return_value = response

    result = await audit.process(context)
    assert result.success is True
    repos.async_get.assert_called_once_with(
        owner="camptocamp",
        repo="archived-repo",
    )


@pytest.mark.asyncio
async def test_use_python_version_lazy_install(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """_use_python_version should lazily install the Python version and add the pyenv bin to PATH."""
    from github_app_geo_project.module.audit import _use_python_version

    monkeypatch.setenv("PYENV_ROOT", str(tmp_path))
    bin_dir = tmp_path / "versions" / "3.12.10" / "bin"
    bin_dir.mkdir(parents=True)

    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"Python 3.12.10", b""))
    mock_proc.returncode = 0

    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.ensure_pyenv_python",
            new=AsyncMock(),
        ) as mock_ensure,
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)),
    ):
        env = await _use_python_version("3.12", anyio.Path(str(tmp_path)))

    mock_ensure.assert_awaited_once_with("3.12")
    assert str(bin_dir) in env["PATH"]


def test_get_fnm_root_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """get_fnm_root should honor the FNM_DIR environment variable."""
    monkeypatch.setenv("FNM_DIR", str(tmp_path / "custom-fnm"))

    assert str(audit_utils.get_fnm_root()) == str(tmp_path / "custom-fnm")


@pytest.mark.asyncio
async def test_find_node_version_spec_nvmrc_priority(tmp_path: Path) -> None:
    """`.nvmrc` should take precedence over `.node-version` and `.tool-versions`."""
    (tmp_path / ".nvmrc").write_text("24\n")
    (tmp_path / ".node-version").write_text("20.11.0\n")
    (tmp_path / ".tool-versions").write_text("nodejs 18.20.0\n")

    assert await audit_utils.find_node_version_spec(anyio.Path(str(tmp_path))) == "24"


@pytest.mark.asyncio
async def test_find_node_version_spec_fallbacks(tmp_path: Path) -> None:
    """An empty `.nvmrc` should fall back to `.node-version`, then to the `.tool-versions` `nodejs` entry."""
    (tmp_path / ".nvmrc").write_text("\n")
    (tmp_path / ".node-version").write_text("v20.11.0\n")
    assert await audit_utils.find_node_version_spec(anyio.Path(str(tmp_path))) == "v20.11.0"

    (tmp_path / ".node-version").unlink()
    (tmp_path / ".tool-versions").write_text("python 3.13.0\nnodejs 22.14.0\n")
    assert await audit_utils.find_node_version_spec(anyio.Path(str(tmp_path))) == "22.14.0"


@pytest.mark.asyncio
async def test_find_node_version_spec_none(tmp_path: Path) -> None:
    """Without any version file the Node.js version specification should be None."""
    assert await audit_utils.find_node_version_spec(anyio.Path(str(tmp_path))) is None


@pytest.mark.asyncio
async def test_ensure_fnm_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ensure_fnm_node should run `fnm install` in the fnm root."""
    monkeypatch.setenv("FNM_DIR", str(tmp_path / "fnm"))
    audit_utils._NODE_INSTALL_LOCKS.clear()

    with patch(
        "github_app_geo_project.module.utils.run_timeout",
        new=AsyncMock(return_value=("", True, None)),
    ) as mock_run_timeout:
        assert await audit_utils.ensure_fnm_node("24.11.0") is True

    assert mock_run_timeout.await_args_list[0].args[0] == ["fnm", "install", "24.11.0"]
    assert (tmp_path / "fnm").is_dir()
    audit_utils._NODE_INSTALL_LOCKS.clear()


@pytest.mark.asyncio
async def test_ensure_fnm_node_concurrent_single_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Concurrent ensures of the same version should be serialized by the per-version lock."""
    monkeypatch.setenv("FNM_DIR", str(tmp_path / "fnm"))
    audit_utils._NODE_INSTALL_LOCKS.clear()
    running = 0
    max_running = 0

    async def fake_run_timeout(command, *args, **kwargs):
        nonlocal running, max_running
        running += 1
        max_running = max(max_running, running)
        await asyncio.sleep(0.01)
        running -= 1
        return ("", True, None)

    with patch(
        "github_app_geo_project.module.utils.run_timeout",
        side_effect=fake_run_timeout,
    ):
        await asyncio.gather(
            audit_utils.ensure_fnm_node("24.11.0"),
            audit_utils.ensure_fnm_node("24.11.0"),
        )

    assert max_running == 1
    audit_utils._NODE_INSTALL_LOCKS.clear()


@pytest.mark.asyncio
async def test_select_node_version_success(tmp_path: Path) -> None:
    """_select_node_version should install the pinned version and prepend its bin directory to PATH."""
    (tmp_path / ".nvmrc").write_text("24\n")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "github_app_geo_project.module.audit.utils.ensure_fnm_node",
            new=AsyncMock(return_value=True),
        ) as mock_ensure,
        patch(
            "github_app_geo_project.module.utils.run_timeout",
            new=AsyncMock(
                return_value=("/opt/fnm/node-versions/v24.11.0/installation/bin/node\n", True, None),
            ),
        ) as mock_run_timeout,
    ):
        await audit_utils._select_node_version(env, anyio.Path(str(tmp_path)))

    mock_ensure.assert_awaited_once_with("24")
    assert mock_run_timeout.await_args_list[0].args[0] == [
        "fnm",
        "exec",
        "--using=24",
        "--",
        "node",
        "-p",
        "process.execPath",
    ]
    assert env["PATH"] == "/opt/fnm/node-versions/v24.11.0/installation/bin:/usr/bin"


@pytest.mark.asyncio
async def test_select_node_version_no_file(tmp_path: Path) -> None:
    """Without a version file _select_node_version should leave the environment untouched."""
    env = {"PATH": "/usr/bin"}

    with patch(
        "github_app_geo_project.module.audit.utils.ensure_fnm_node",
        new=AsyncMock(),
    ) as mock_ensure:
        await audit_utils._select_node_version(env, anyio.Path(str(tmp_path)))

    mock_ensure.assert_not_awaited()
    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_select_node_version_install_failure(tmp_path: Path) -> None:
    """On fnm install failure the system Node.js should be kept."""
    (tmp_path / ".nvmrc").write_text("24\n")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "github_app_geo_project.module.audit.utils.ensure_fnm_node",
            new=AsyncMock(return_value=False),
        ),
        patch("github_app_geo_project.module.utils.run_timeout", new=AsyncMock()) as mock_run_timeout,
    ):
        await audit_utils._select_node_version(env, anyio.Path(str(tmp_path)))

    mock_run_timeout.assert_not_awaited()
    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_select_node_version_resolve_failure(tmp_path: Path) -> None:
    """If the installation directory cannot be resolved the system Node.js should be kept."""
    (tmp_path / ".nvmrc").write_text("lts/*\n")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "github_app_geo_project.module.audit.utils.ensure_fnm_node",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "github_app_geo_project.module.utils.run_timeout",
            new=AsyncMock(return_value=(None, False, None)),
        ),
    ):
        await audit_utils._select_node_version(env, anyio.Path(str(tmp_path)))

    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_npm_audit_fix_uses_env(tmp_path: Path) -> None:
    """_npm_audit_fix should run npm with the given environment (the repository Node.js version)."""
    (tmp_path / "package.json").write_text('{"dependencies": {"foo": "^1.0.0"}}')
    (tmp_path / "package-lock.json").write_text("{}")
    env = {"PATH": "/opt/fnm/node-versions/v24.11.0/installation/bin:/usr/bin"}

    with patch(
        "github_app_geo_project.module.utils.run_timeout",
        new=AsyncMock(return_value=("", True, None)),
    ) as mock_run_timeout:
        messages, success = await audit_utils._npm_audit_fix(
            {"package-lock.json": {"[HIGH] foo@1.0.0: SNYK-JS-FOO-1"}},
            [],
            anyio.Path(str(tmp_path)),
            env,
        )

    assert success is True
    assert messages == "[HIGH] foo@1.0.0: SNYK-JS-FOO-1"
    assert mock_run_timeout.await_args_list[0].args[0] == ["npm", "audit", "fix"]
    assert mock_run_timeout.await_args_list[0].args[1] is env


def _make_snyk_vulnerability() -> VulnerabilityData:
    return VulnerabilityData(
        file="pyproject.toml",
        package_name="django",
        package_version="3.2.0",
        package_manager="pip",
        severity="high",
        snyk_id="SNYK-PYTHON-DJANGO-123456",
        cve_ids=["CVE-2024-12345"],
        cwe_ids=["CWE-79"],
        title="[HIGH] django@3.2.0: [CVE-2024-12345]",
        fixed_in=["3.2.1"],
        is_upgradable=True,
        is_patchable=False,
    )


def _make_freeze_proc_mock() -> MagicMock:
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b""))
    mock_proc.wait = AsyncMock(return_value=None)
    mock_proc.returncode = 0
    return mock_proc


@pytest.mark.parametrize(
    ("local_config", "expect_fix"),
    [({}, False), ({"auto-fix": False}, False), ({"auto-fix": True}, True)],
)
@pytest.mark.asyncio
async def test_snyk_auto_fix(tmp_path: Path, local_config: dict, expect_fix: bool) -> None:
    """With `auto-fix` disabled the fix commands are skipped, the vulnerabilities are only reported."""
    env = {"HOME": str(tmp_path), "PATH": "/usr/bin"}
    test_result = (
        {"high": 1},
        {"high": 1},
        {"pyproject.toml": "[HIGH] django@3.2.0: SNYK-JS-FOO-1"},
        {},
        False,
        {"pyproject.toml": [_make_snyk_vulnerability()]},
    )

    with (
        patch.object(audit_utils, "_select_java_version", new=AsyncMock()),
        patch.object(audit_utils, "_select_node_version", new=AsyncMock()),
        patch.object(audit_utils, "_install_requirements_dependencies", new=AsyncMock()),
        patch.object(audit_utils, "_install_pipenv_dependencies", new=AsyncMock()),
        patch.object(audit_utils, "_install_poetry_dependencies", new=AsyncMock(return_value=[])),
        patch.object(audit_utils, "_snyk_monitor", new=AsyncMock(return_value=True)),
        patch.object(audit_utils, "snyk_cleanup_stale_projects", new=AsyncMock()),
        patch.object(audit_utils, "_snyk_test", new=AsyncMock(return_value=test_result)),
        patch.object(audit_utils, "_snyk_fix", new=AsyncMock(return_value=(True, None))) as mock_snyk_fix,
        patch.object(
            audit_utils, "_npm_audit_fix", new=AsyncMock(return_value=(None, True))
        ) as mock_npm_audit_fix,
        patch.object(audit_utils, "_cleanup_poetry_envs", new=AsyncMock()),
        patch.object(
            audit_utils.module_utils,
            "run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ),
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_make_freeze_proc_mock()),
        ),
    ):
        result = await audit_utils.snyk(
            "1.0",
            "owner",
            "repository",
            {},
            {},
            {},
            local_config,
            "https://example.com/logs/1",
            env,
            anyio.Path(str(tmp_path)),
        )

    if expect_fix:
        mock_snyk_fix.assert_awaited_once()
        mock_npm_audit_fix.assert_awaited_once()
        assert not any(message.title == "Auto-fix disabled" for message in result.messages)
    else:
        mock_snyk_fix.assert_not_awaited()
        mock_npm_audit_fix.assert_not_awaited()
        assert any(message.title == "Auto-fix disabled" for message in result.messages)
    # No fix applied and no rescan: nothing fixed, the vulnerabilities of the scan are reported
    assert result.fixed_vulnerabilities == {}
    assert result.pinned_vulnerabilities == {}
    assert result.vulnerabilities == test_result[5]
    assert result.success is True


async def _aiter(items):
    for item in items:
        yield item


def _make_request_failed_exception(status_code: int) -> githubkit.exception.RequestFailed:
    response = MagicMock()
    response.status_code = status_code
    return githubkit.exception.RequestFailed(response)


def _named_mock(**attributes) -> MagicMock:
    mock = MagicMock()
    for key, value in attributes.items():
        setattr(mock, key, value)
    return mock


def _cleanup_context(known_versions, branches, issues, outputs) -> Mock:
    """Build a mocked ProcessContext for the audit cleanup job."""
    context = Mock()
    context.module_event_data = _EventData(type="cleanup", known_versions=known_versions)
    context.github_event_data = {}
    context.issue_data = (
        "- [ ] <!-- outdated --> Check outdated version\n"
        "- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"
        "- [ ] <!-- dpkg --> Update dpkg packages\n"
    )
    context.service_url = "https://example.com/"
    context.job_id = 42
    context.github_project = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.github_project.application.slug = "my-app"
    context.github_project.aio_github.paginate = MagicMock(side_effect=[_aiter(branches), _aiter(issues)])
    context.github_project.aio_github.rest.issues.async_update = AsyncMock()

    outputs_result = MagicMock()
    outputs_result.scalars.return_value = outputs
    context.session = MagicMock()
    context.session.execute = AsyncMock(return_value=outputs_result)
    context.session.delete = AsyncMock()
    context.session.commit = AsyncMock()
    return context


@pytest.mark.asyncio
async def test_process_cleanup_everything_clean() -> None:
    """Without leftovers the cleanup job reports a clean situation."""
    context = _cleanup_context(
        ["1.2", "master"],
        [_named_mock(name="ghci/audit/snyk/1.2"), _named_mock(name="ghci/audit/renovate/master")],
        [],
        [_named_mock(name="snyk-1.2")],
    )

    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.close_pull_request_issues",
            new=AsyncMock(),
        ) as mock_close,
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_removed_references",
            new=AsyncMock(return_value=([], True)),
        ),
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_stale_projects_by_age",
            new=AsyncMock(return_value=([], True)),
        ),
    ):
        result = await Audit().process(context)

    mock_close.assert_not_awaited()
    context.session.delete.assert_not_awaited()
    assert result.success is True
    assert result.check_output == {"summary": "Cleanup: Everything is clean"}
    assert result.updated_transversal_status is True
    assert result.intermediate_status is not None
    cleanup_status = result.intermediate_status.status.types["Cleanup"]
    assert cleanup_status.status == "success"
    assert cleanup_status.summary == "Everything is clean"


@pytest.mark.asyncio
async def test_process_cleanup_removes_leftovers() -> None:
    """The cleanup job closes the leftovers of versions removed from SECURITY.md and reports them."""
    issues = [
        _named_mock(
            title="Pull request Audit Snyk check/fix 1.1 is open for 14 days",
            number=101,
            html_url="https://github.com/owner/repo/issues/101",
        ),
        _named_mock(
            title="Pull request Audit Cleanup Renovate configuration for version 2.0 is open for 7 days",
            number=102,
            html_url="https://github.com/owner/repo/issues/102",
        ),
        _named_mock(
            title="Pull request Audit Snyk check/fix 1.2 is open for 6 days",
            number=103,
            html_url="https://github.com/owner/repo/issues/103",
        ),
    ]
    context = _cleanup_context(
        ["1.2", "master"],
        [
            _named_mock(name="ghci/audit/snyk/1.1"),
            _named_mock(name="ghci/audit/renovate/2.0"),
            _named_mock(name="ghci/audit/snyk/1.2"),
        ],
        issues,
        [_named_mock(name="snyk-1.1"), _named_mock(name="snyk-1.2")],
    )

    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.close_pull_request_issues",
            new=AsyncMock(),
        ) as mock_close,
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_removed_references",
            new=AsyncMock(return_value=(["Snyk reference `1.1` (2 projects)"], True)),
        ) as mock_snyk_cleanup,
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_stale_projects_by_age",
            new=AsyncMock(return_value=(["2 stale Snyk project(s) not monitored since 7 days"], True)),
        ) as mock_snyk_stale,
    ):
        result = await Audit().process(context)

    assert [list(call.args)[:2] for call in mock_close.await_args_list] == [
        ["ghci/audit/snyk/1.1", "Audit Snyk check/fix 1.1"],
        ["ghci/audit/renovate/2.0", "Audit Cleanup Renovate configuration for version 2.0"],
    ]
    closed_issues = [
        call.kwargs["issue_number"]
        for call in context.github_project.aio_github.rest.issues.async_update.await_args_list
    ]
    assert closed_issues == [101, 102]
    context.session.delete.assert_awaited_once()
    assert context.session.delete.await_args is not None
    assert context.session.delete.await_args.args[0].name == "snyk-1.1"
    context.session.commit.assert_awaited_once()
    mock_snyk_cleanup.assert_awaited_once_with("owner", "repo", ["1.2", "master"])
    mock_snyk_stale.assert_awaited_once_with("owner", "repo")
    assert result.check_output is not None
    assert result.check_output["summary"] == "Cleanup: 7 leftover(s) removed"
    assert "snyk-1.1" in result.check_output["text"]
    assert "Snyk reference `1.1`" in result.check_output["text"]
    assert "2 stale Snyk project(s)" in result.check_output["text"]
    assert result.intermediate_status is not None
    assert result.intermediate_status.status.types["Cleanup"].summary == "7 leftover(s) removed"


@pytest.mark.asyncio
async def test_process_cleanup_without_known_versions_clears_dashboard() -> None:
    """Without any known version (SECURITY.md removed) all the checks are removed from the dashboard."""
    context = _cleanup_context(None, [], [], [])

    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.close_pull_request_issues",
            new=AsyncMock(),
        ),
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_removed_references",
            new=AsyncMock(return_value=([], True)),
        ) as mock_snyk_cleanup,
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_stale_projects_by_age",
            new=AsyncMock(return_value=([], True)),
        ),
    ):
        result = await Audit().process(context)

    mock_snyk_cleanup.assert_awaited_once_with("owner", "repo", [])
    assert result.dashboard is not None
    assert "<!-- outdated -->" not in result.dashboard
    assert "<!-- snyk -->" not in result.dashboard
    assert "<!-- dpkg -->" not in result.dashboard


@pytest.mark.asyncio
async def test_process_cleanup_snyk_api_error_reported() -> None:
    """A Snyk API error is reported in the cleanup summary and the transversal status is in error."""
    context = _cleanup_context(["1.2", "master"], [], [], [])

    with (
        patch(
            "github_app_geo_project.module.audit.module_utils.close_pull_request_issues",
            new=AsyncMock(),
        ),
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_removed_references",
            new=AsyncMock(return_value=([], False)),
        ),
        patch(
            "github_app_geo_project.module.audit.utils.snyk_cleanup_stale_projects_by_age",
            new=AsyncMock(return_value=([], True)),
        ),
    ):
        result = await Audit().process(context)

    assert result.success is True
    assert result.check_output is not None
    assert result.check_output["summary"] == "Cleanup: Snyk API error(s)"
    assert "Snyk API error(s), see the logs" in result.check_output["text"]
    assert result.intermediate_status is not None
    cleanup_status = result.intermediate_status.status.types["Cleanup"]
    assert cleanup_status.status == "error"
    assert cleanup_status.summary == "Snyk API error(s)"


@pytest.mark.asyncio
async def test_update_transversal_status_prunes_stale_types() -> None:
    """The stale transversal status types are pruned when known_types is set."""
    context = Mock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"

    transversal = _TransversalStatus(
        updated={"owner/repo": dt.datetime.now(dt.UTC)},
        repositories={
            "owner/repo": _TransversalStatusRepo(
                types={
                    "Outdated version": _TransversalStatusTool(name="Outdated version", status="success"),
                    "Snyk check/fix 1.1": _TransversalStatusTool(name="Snyk check/fix 1.1", status="success"),
                    "Dpkg 1.1": _TransversalStatusTool(name="Dpkg 1.1", status="success"),
                }
            )
        },
    )
    intermediate = _IntermediateStatus(
        status=_TransversalStatusRepo(),
        known_types=["Outdated version", "Snyk check/fix 1.2", "Cleanup"],
    )

    result = await Audit().update_transversal_status(context, intermediate, transversal)

    types = result.repositories["owner/repo"].types
    assert "Outdated version" in types
    assert "Snyk check/fix 1.1" not in types
    assert "Dpkg 1.1" not in types


@pytest.mark.asyncio
async def test_update_transversal_status_merges_without_known_types() -> None:
    """Without known_types the transversal status types are only merged, never pruned."""
    context = Mock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"

    transversal = _TransversalStatus(
        updated={"owner/repo": dt.datetime.now(dt.UTC)},
        repositories={
            "owner/repo": _TransversalStatusRepo(
                types={
                    "Snyk check/fix 1.1": _TransversalStatusTool(name="Snyk check/fix 1.1", status="success"),
                }
            )
        },
    )
    intermediate = _IntermediateStatus(
        status=_TransversalStatusRepo(
            types={
                "Snyk check/fix 1.2": _TransversalStatusTool(name="Snyk check/fix 1.2", status="success"),
            }
        ),
    )

    result = await Audit().update_transversal_status(context, intermediate, transversal)

    types = result.repositories["owner/repo"].types
    assert "Snyk check/fix 1.1" in types
    assert "Snyk check/fix 1.2" in types


@pytest.mark.asyncio
async def test_process_fan_out_prunes_stale_sections_and_types() -> None:
    """The fan-out removes the legacy dashboard sections and prepares the transversal status pruning."""
    context = Mock()
    context.module_event_data = _EventData(snyk=True, dpkg=False)
    context.github_event_data = {}
    context.module_config = {"version-mapping": {}}
    context.issue_data = (
        "- [ ] <!-- outdated --> Check outdated version\n"
        "- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"
        "\n"
        "<!-- vulns-1.0 -->\n"
        "- old vulnerability\n"
        "<!-- /vulns-1.0 -->\n"
        "=== 1.1\n"
        "- [HIGH] foo@1.0\n"
        "=== 1.2\n"
        "- [HIGH] bar@2.0\n"
    )
    context.github_project = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.github_project.default_branch = AsyncMock(return_value="master")

    security_file = MagicMock(spec=githubkit_schemas.latest.models.ContentFile)
    security_file.content = base64.b64encode(b"# Security policy").decode("utf-8")

    async def async_get_content(owner, repo, path):
        if path == "SECURITY.md":
            return MagicMock(parsed_data=security_file)
        raise _make_request_failed_exception(404)

    context.github_project.aio_github.rest.repos.async_get_content = AsyncMock(side_effect=async_get_content)

    with patch("github_app_geo_project.module.audit.security_md.Security") as mock_security:
        mock_security.return_value.branches.return_value = ["1.2"]
        result = await Audit().process(context)

    assert result.dashboard is not None
    assert "vulns-1.0" not in result.dashboard
    assert "=== 1.1" not in result.dashboard
    assert "foo@1.0" not in result.dashboard
    assert "=== 1.2" in result.dashboard
    assert result.intermediate_status is not None
    assert result.intermediate_status.known_types == [
        "Outdated version",
        "Snyk check/fix 1.2",
        "Trivy filesystem check/fix 1.2",
        "Cleanup",
    ]
    assert result.actions[0].data == _EventData(type="cleanup", known_versions=["1.2", "master"])
    assert result.actions[1].data == _EventData(type="snyk", version="1.2")


def test_get_transversal_dashboard_cleanup_is_global() -> None:
    """Types without a version suffix (like Cleanup) are displayed as global types."""
    context = module.TransversalDashboardContext(
        status=_TransversalStatus(
            updated={"owner/repo": dt.datetime.now(dt.UTC)},
            repositories={
                "owner/repo": _TransversalStatusRepo(
                    types={
                        "Cleanup": _TransversalStatusTool(
                            name="Cleanup",
                            summary="Everything is clean",
                            status="success",
                        ),
                        "Snyk check/fix 1.2": _TransversalStatusTool(
                            name="Snyk check/fix 1.2", status="success"
                        ),
                    }
                )
            },
        ),
        params={},
    )

    output = Audit().get_transversal_dashboard(context)

    repository = output.data["repositories"][0]
    assert [item["name"] for item in repository["global_types"]] == ["Cleanup"]
    assert [branch["name"] for branch in repository["branches"]] == ["1.2"]


def test_remove_dashboard_vuln_section() -> None:
    """The legacy vulnerability sections are removed in both known formats."""
    issue_check = module_utils.DashboardIssue(
        "- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"
        "\n"
        "<!-- vulns-1.0 -->\n"
        "- old vulnerability\n"
        "<!-- /vulns-1.0 -->\n"
        "=== 1.1\n"
        "- [HIGH] foo@1.0\n"
        "=== 1.2\n"
        "- [HIGH] bar@2.0\n"
    )

    assert _remove_dashboard_vuln_section(issue_check, "1.0") is True
    assert _remove_dashboard_vuln_section(issue_check, "1.1") is True
    assert _remove_dashboard_vuln_section(issue_check, "9.9") is False

    result = issue_check.to_string()
    assert "vulns-1.0" not in result
    assert "old vulnerability" not in result
    assert "=== 1.1" not in result
    assert "foo@1.0" not in result
    assert "=== 1.2" in result
    assert "bar@2.0" in result


def test_dashboard_vuln_section_versions() -> None:
    """The versions of the legacy vulnerability sections are extracted from the dashboard issue."""
    issue_check = module_utils.DashboardIssue(
        "=== 1.1\n==== pyproject.toml\n- [HIGH] foo@1.0\n<!-- vulns-1.0 -->\n<!-- /vulns-1.0 -->\n"
    )

    assert _dashboard_vuln_section_versions(issue_check) == {"1.1", "1.0"}


_SNYK_ORG_UUID = "3e40ec98-5983-4b1d-828e-9be5e2f3f3bd"


class _FakeSnykResponse:
    """Fake aiohttp response for the Snyk REST API."""

    def __init__(self, payload: Any = None, ok: bool = True, status: int = 200, text: str = "") -> None:
        self.payload = payload
        self.ok = ok
        self.status = status
        self.text_content = text

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    async def text(self) -> str:
        return self.text_content


class _FakeSnykSession:
    """Fake aiohttp session recording the requests sent to the Snyk REST API."""

    def __init__(self, get_payloads: list[Any] | None = None, delete_ok: bool = True) -> None:
        self.get_payloads = list(get_payloads or [])
        self.delete_ok = delete_ok
        self.requests: list[tuple[str, str, Any]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def get(self, url: str, params: Any = None) -> _FakeSnykResponse:
        self.requests.append(("GET", url, params))
        return _FakeSnykResponse(self.get_payloads.pop(0) if self.get_payloads else {"data": []})

    def delete(self, url: str, params: Any = None) -> _FakeSnykResponse:
        self.requests.append(("DELETE", url, params))
        return _FakeSnykResponse(None, ok=self.delete_ok, status=204 if self.delete_ok else 500)


def _snyk_project(
    project_id: str,
    name: str,
    target_reference: str,
    origin: str = "cli",
    target_file: str = "pyproject.toml",
) -> dict[str, Any]:
    return {
        "id": project_id,
        "attributes": {
            "name": name,
            "target_reference": target_reference,
            "origin": origin,
            "target_file": target_file,
        },
    }


def test_snyk_api_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Snyk API cleanup requires the kill switch and a token."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", None)
    assert audit_utils._snyk_api_config() is None

    monkeypatch.setattr(settings.audit, "snyk_token", "test-token")
    assert audit_utils._snyk_api_config() == ("test-token", "https://api.snyk.io")

    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", False)
    assert audit_utils._snyk_api_config() is None


@pytest.mark.asyncio
async def test_snyk_api_get_all_pagination() -> None:
    """The paginated collections are fully traversed following the next links."""
    session = _FakeSnykSession(
        get_payloads=[
            {"data": [{"id": "1"}], "links": {"next": "/rest/orgs/org/projects?starting_after=1"}},
            {"data": [{"id": "2"}], "links": {}},
        ]
    )

    entries = await audit_utils._snyk_api_get_all(
        session,
        "https://api.snyk.io",
        "/orgs/org/projects",
        {"target_reference": "1.2"},
    )

    assert [entry["id"] for entry in entries] == ["1", "2"]
    assert session.requests[0][1] == "https://api.snyk.io/rest/orgs/org/projects"
    assert ("target_reference", "1.2") in session.requests[0][2]
    assert ("version", audit_utils._SNYK_API_VERSION) in session.requests[0][2]
    assert ("limit", "100") in session.requests[0][2]
    assert session.requests[1][1] == "https://api.snyk.io/rest/orgs/org/projects?starting_after=1"
    assert session.requests[1][2] is None


@pytest.mark.asyncio
async def test_snyk_api_get_all_error() -> None:
    """An API error interrupts the pagination and raises `_SnykApiError`."""

    class _ErrorSession(_FakeSnykSession):
        def get(self, url: str, params: Any = None) -> _FakeSnykResponse:
            self.requests.append(("GET", url, params))
            return _FakeSnykResponse(None, ok=False, status=500, text="Internal Server Error")

    session = _ErrorSession()
    with pytest.raises(audit_utils._SnykApiError):
        await audit_utils._snyk_api_get_all(session, "https://api.snyk.io", "/orgs", {})

    assert len(session.requests) == 1


def test_snyk_api_datetime() -> None:
    """The API datetimes are formatted with the URL-safe `Z` UTC suffix (no `+00:00` offset)."""
    moment = dt.datetime(2026, 10, 2, 8, 30, 15, 123456, tzinfo=dt.UTC)
    assert audit_utils._snyk_api_datetime(moment) == "2026-10-02T08:30:15Z"

    cet = dt.datetime(2026, 10, 2, 10, 30, 15, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert audit_utils._snyk_api_datetime(cet) == "2026-10-02T08:30:15Z"


@pytest.mark.asyncio
async def test_resolve_snyk_org_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The organization is used as-is when it is a UUID, resolved by slug otherwise."""
    session = _FakeSnykSession(
        get_payloads=[
            {
                "data": [
                    {"id": "org-uuid-1", "attributes": {"slug": "my-org", "name": "My Org"}},
                ],
                "links": {},
            }
        ]
    )

    monkeypatch.setattr(settings.audit, "snyk_org", _SNYK_ORG_UUID)
    assert await audit_utils._resolve_snyk_org_id(session, "https://api.snyk.io") == _SNYK_ORG_UUID
    assert session.requests == []

    monkeypatch.setattr(settings.audit, "snyk_org", "my-org")
    assert await audit_utils._resolve_snyk_org_id(session, "https://api.snyk.io") == "org-uuid-1"

    unknown_session = _FakeSnykSession(get_payloads=[{"data": [], "links": {}}])
    monkeypatch.setattr(settings.audit, "snyk_org", "unknown-org")
    assert await audit_utils._resolve_snyk_org_id(unknown_session, "https://api.snyk.io") is None

    monkeypatch.setattr(settings.audit, "snyk_org", None)
    assert await audit_utils._resolve_snyk_org_id(unknown_session, "https://api.snyk.io") is None


@pytest.mark.asyncio
async def test_resolve_snyk_target_ids() -> None:
    """The targets of the repository are matched on their display name."""
    session = _FakeSnykSession(
        get_payloads=[
            {
                "data": [
                    {"id": "target-1", "attributes": {"display_name": "owner/repo"}},
                    {"id": "target-2", "attributes": {"display_name": "https://github.com/owner/repo.git"}},
                    {"id": "target-3", "attributes": {"display_name": "owner/other"}},
                    {"id": "target-4", "attributes": {}},
                ],
                "links": {},
            }
        ]
    )

    assert await audit_utils._resolve_snyk_target_ids(
        session, "https://api.snyk.io", "org", "owner", "repo"
    ) == ["target-1", "target-2"]


@pytest.mark.asyncio
async def test_snyk_cleanup_stale_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the CLI projects of the reference not refreshed by the monitor run are deleted."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", "test-token")
    monkeypatch.setattr(settings.audit, "snyk_org", _SNYK_ORG_UUID)

    session = _FakeSnykSession()
    projects = [
        _snyk_project("project-1", "owner/repo/old", "1.2", target_file="old-requirements.txt"),
        _snyk_project("project-2", "owner/repo/imported", "1.2", origin="github"),
    ]
    result: list[module_utils.Message] = []

    with (
        patch.object(audit_utils, "_snyk_api_session", return_value=session),
        patch.object(audit_utils, "_resolve_snyk_target_ids", new=AsyncMock(return_value=["target-1"])),
        patch.object(audit_utils, "_snyk_list_projects", new=AsyncMock(return_value=projects)) as mock_list,
    ):
        success = await audit_utils.snyk_cleanup_stale_projects(
            "owner",
            "repo",
            "1.2",
            dt.datetime(2026, 9, 28, tzinfo=dt.UTC),
            result,
        )

    assert success is True
    assert mock_list.await_args is not None
    assert mock_list.await_args.args[4] == [
        ("target_reference", "1.2"),
        ("cli_monitored_before", "2026-09-28T00:00:00Z"),
    ]
    delete_requests = [request for request in session.requests if request[0] == "DELETE"]
    assert len(delete_requests) == 1
    assert delete_requests[0][1] == f"https://api.snyk.io/rest/orgs/{_SNYK_ORG_UUID}/projects/project-1"
    assert len(result) == 1
    assert result[0].title == "Snyk projects cleanup"
    assert "old-requirements.txt" in result[0].to_markdown()


@pytest.mark.asyncio
async def test_snyk_cleanup_removed_references(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI projects of the references that are not supported anymore are deleted and reported."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", "test-token")
    monkeypatch.setattr(settings.audit, "snyk_org", _SNYK_ORG_UUID)

    session = _FakeSnykSession()
    projects = [
        _snyk_project("project-1", "owner/repo/a", "1.1"),
        _snyk_project("project-2", "owner/repo/b", "1.1"),
        _snyk_project("project-3", "owner/repo/c", "1.2"),
        _snyk_project("project-4", "owner/repo/d", "master"),
        _snyk_project("project-5", "owner/repo/e", "custom-ref", origin="github"),
    ]

    with (
        patch.object(audit_utils, "_snyk_api_session", return_value=session),
        patch.object(audit_utils, "_resolve_snyk_target_ids", new=AsyncMock(return_value=["target-1"])),
        patch.object(audit_utils, "_snyk_list_projects", new=AsyncMock(return_value=projects)),
    ):
        removed, success = await audit_utils.snyk_cleanup_removed_references(
            "owner", "repo", ["1.2", "master"]
        )

    assert removed == ["Snyk reference `1.1` (2 projects)"]
    assert success is True
    assert sorted(request[1] for request in session.requests if request[0] == "DELETE") == [
        f"https://api.snyk.io/rest/orgs/{_SNYK_ORG_UUID}/projects/project-1",
        f"https://api.snyk.io/rest/orgs/{_SNYK_ORG_UUID}/projects/project-2",
    ]


@pytest.mark.asyncio
async def test_snyk_cleanup_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an API token the cleanups are no-ops and no session is created."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", None)

    session_factory = MagicMock()
    result: list[module_utils.Message] = []
    with patch.object(audit_utils, "_snyk_api_session", session_factory):
        stale_success = await audit_utils.snyk_cleanup_stale_projects(
            "owner",
            "repo",
            "1.2",
            dt.datetime(2026, 9, 28, tzinfo=dt.UTC),
            result,
        )
        removed, removed_success = await audit_utils.snyk_cleanup_removed_references("owner", "repo", ["1.2"])
        by_age_report, by_age_success = await audit_utils.snyk_cleanup_stale_projects_by_age("owner", "repo")

    session_factory.assert_not_called()
    assert stale_success is True
    assert result == []
    assert (removed, removed_success) == ([], True)
    assert (by_age_report, by_age_success) == ([], True)


@pytest.mark.asyncio
async def test_snyk_delete_projects_failure() -> None:
    """A deletion failure is logged and the project is not reported as deleted."""
    session = _FakeSnykSession(delete_ok=False)

    deleted, failures = await audit_utils._snyk_delete_projects(
        session,
        "https://api.snyk.io",
        "org",
        [_snyk_project("project-1", "owner/repo/a", "1.1")],
    )

    assert deleted == []
    assert failures == 1


@pytest.mark.asyncio
async def test_snyk_monitor_returns_success() -> None:
    """_snyk_monitor returns the success of the monitor command, used to gate the stale projects cleanup."""
    with patch.object(
        audit_utils.module_utils,
        "run_timeout",
        new=AsyncMock(return_value=("", False, None)),
    ):
        success = await audit_utils._snyk_monitor("1.2", {}, {}, [], {}, anyio.Path("."))

    assert success is False


@pytest.mark.asyncio
async def test_snyk_cleanup_stale_projects_by_age(monkeypatch: pytest.MonkeyPatch) -> None:
    """The projects not re-monitored since too long are deleted, whatever the monitor run result."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", "test-token")
    monkeypatch.setattr(settings.audit, "snyk_org", _SNYK_ORG_UUID)
    monkeypatch.setattr(settings.audit, "snyk_api_stale_age", dt.timedelta(days=7))

    session = _FakeSnykSession()
    projects = [
        _snyk_project("project-1", "tmpab12cd34", "3.31"),
        _snyk_project("project-2", "tmpab12cd34/core", "3.31"),
        _snyk_project("project-3", "owner/repo/imported", "3.31", origin="github"),
    ]

    with (
        patch.object(audit_utils, "_snyk_api_session", return_value=session),
        patch.object(audit_utils, "_resolve_snyk_target_ids", new=AsyncMock(return_value=["target-1"])),
        patch.object(audit_utils, "_snyk_list_projects", new=AsyncMock(return_value=projects)) as mock_list,
    ):
        report, success = await audit_utils.snyk_cleanup_stale_projects_by_age("owner", "repo")

    assert mock_list.await_args is not None
    extra_params = dict(mock_list.await_args.args[4])
    assert set(extra_params) == {"cli_monitored_before"}
    monitored_before = dt.datetime.strptime(
        extra_params["cli_monitored_before"], "%Y-%m-%dT%H:%M:%SZ"
    ).replace(tzinfo=dt.UTC)
    expected = dt.datetime.now(dt.UTC) - dt.timedelta(days=7)
    assert abs(monitored_before - expected) < dt.timedelta(minutes=1)

    assert sorted(request[1] for request in session.requests if request[0] == "DELETE") == [
        f"https://api.snyk.io/rest/orgs/{_SNYK_ORG_UUID}/projects/project-1",
        f"https://api.snyk.io/rest/orgs/{_SNYK_ORG_UUID}/projects/project-2",
    ]
    assert report == ["2 stale Snyk project(s) not monitored since 7 days"]
    assert success is True


@pytest.mark.asyncio
async def test_snyk_cleanup_api_error_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Snyk API error is reported to the caller instead of pretending everything is clean."""
    monkeypatch.setattr(settings.audit, "snyk_api_cleanup", True)
    monkeypatch.setattr(settings.audit, "snyk_token", "test-token")
    monkeypatch.setattr(settings.audit, "snyk_org", _SNYK_ORG_UUID)

    class _ErrorSession(_FakeSnykSession):
        def get(self, url: str, params: Any = None) -> _FakeSnykResponse:
            self.requests.append(("GET", url, params))
            return _FakeSnykResponse(None, ok=False, status=400, text="Bad request")

    session = _ErrorSession()
    result: list[module_utils.Message] = []
    with patch.object(audit_utils, "_snyk_api_session", return_value=session):
        removed, removed_success = await audit_utils.snyk_cleanup_removed_references("owner", "repo", ["1.2"])
        by_age_report, by_age_success = await audit_utils.snyk_cleanup_stale_projects_by_age("owner", "repo")
        stale_success = await audit_utils.snyk_cleanup_stale_projects(
            "owner",
            "repo",
            "1.2",
            dt.datetime(2026, 10, 2, tzinfo=dt.UTC),
            result,
        )

    assert (removed, removed_success) == ([], False)
    assert (by_age_report, by_age_success) == ([], False)
    assert stale_success is False
    assert len(result) == 1
    assert result[0].title == "Snyk projects cleanup"
    assert "cleanup failed" in result[0].to_markdown()


@pytest.mark.asyncio
async def test_resolve_snyk_org_id_single_org_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a configured organization, the single accessible organization is used."""
    monkeypatch.setattr(settings.audit, "snyk_org", None)

    with patch.object(
        audit_utils,
        "_snyk_api_get_all",
        new=AsyncMock(return_value=[{"id": "org-uuid-1", "attributes": {"slug": "my-org"}}]),
    ):
        assert await audit_utils._resolve_snyk_org_id(MagicMock(), "https://api.snyk.io") == "org-uuid-1"

    with patch.object(
        audit_utils,
        "_snyk_api_get_all",
        new=AsyncMock(
            return_value=[
                {"id": "org-uuid-1", "attributes": {"slug": "org-1"}},
                {"id": "org-uuid-2", "attributes": {"slug": "org-2"}},
            ]
        ),
    ):
        assert await audit_utils._resolve_snyk_org_id(MagicMock(), "https://api.snyk.io") is None


def _vulnerability(
    file_name: str = "pyproject.toml",
    package_name: str = "oauthlib",
    package_version: str = "3.2.2",
    severity: str = "high",
    snyk_id: str = "SNYK-PYTHON-OAUTHLIB-123456",
    cve_ids: list[str] | None = None,
    fixed_in: list[str] | None = None,
) -> VulnerabilityData:
    """Build a vulnerability for the tests."""
    return VulnerabilityData(
        file=file_name,
        package_name=package_name,
        package_version=package_version,
        package_manager="pip",
        severity=severity,
        snyk_id=snyk_id,
        cve_ids=["CVE-2026-12345"] if cve_ids is None else cve_ids,
        cwe_ids=["CWE-79"],
        title=f"[{severity.upper()}] {package_name}@{package_version}: {snyk_id}",
        fixed_in=["4.0.0"] if fixed_in is None else fixed_in,
        is_upgradable=True,
        is_patchable=False,
    )


def test_fixed_vulnerabilities_diff() -> None:
    """Only the vulnerabilities that disappeared between the two scans are reported as fixed."""
    fixed_vuln = _vulnerability()
    remaining_vuln = _vulnerability(
        package_name="django",
        package_version="3.2.0",
        severity="medium",
        snyk_id="SNYK-PYTHON-DJANGO-654321",
        cve_ids=["CVE-2026-54321"],
    )
    before = {"pyproject.toml": [fixed_vuln, remaining_vuln]}
    after = {"pyproject.toml": [remaining_vuln]}

    fixed = audit_utils.fixed_vulnerabilities(before, after)

    assert list(fixed) == ["pyproject.toml"]
    assert fixed["pyproject.toml"] == [fixed_vuln]


def test_fixed_vulnerabilities_empty_when_nothing_changed() -> None:
    """Without any change between the two scans nothing is reported as fixed."""
    vulnerabilities = {"pyproject.toml": [_vulnerability()]}

    assert audit_utils.fixed_vulnerabilities(vulnerabilities, vulnerabilities) == {}
    assert audit_utils.fixed_vulnerabilities({}, vulnerabilities) == {}


def test_fixed_vulnerabilities_upgraded_version_is_fixed() -> None:
    """A vulnerability still present on an upgraded version is not the fixed one."""
    before = {"requirements.txt": [_vulnerability(package_version="3.2.2")]}
    after = {"requirements.txt": [_vulnerability(package_version="4.0.0")]}

    fixed = audit_utils.fixed_vulnerabilities(before, after)

    assert [vuln.package_version for vuln in fixed["requirements.txt"]] == ["3.2.2"]


def test_fixed_vulnerabilities_sorted_by_severity() -> None:
    """The fixed vulnerabilities are sorted by descending severity, then by package name."""
    low = _vulnerability(package_name="aaa", severity="low", snyk_id="SNYK-1")
    critical = _vulnerability(package_name="zzz", severity="critical", snyk_id="SNYK-2")
    high = _vulnerability(package_name="mmm", severity="high", snyk_id="SNYK-3")

    fixed = audit_utils.fixed_vulnerabilities({"b.txt": [low], "a.txt": [high, critical]}, {})

    assert list(fixed) == ["a.txt", "b.txt"]
    assert [vuln.severity for vuln in fixed["a.txt"]] == ["critical", "high"]


def test_fixed_vulnerabilities_markdown() -> None:
    """The fixed vulnerabilities are rendered as a markdown list of the pull request body."""
    markdown = _fixed_vulnerabilities_markdown(
        {
            "pyproject.toml": [
                _vulnerability(cve_ids=["CVE-2026-12345", "CVE-2026-67890"]),
            ],
        },
    )

    assert markdown == (
        "## Fixed vulnerabilities\n"
        "\n"
        "- **[HIGH]** `oauthlib` `3.2.2` in `pyproject.toml` — "
        "[CVE-2026-12345](https://nvd.nist.gov/vuln/detail/CVE-2026-12345), "
        "[CVE-2026-67890](https://nvd.nist.gov/vuln/detail/CVE-2026-67890), "
        "[SNYK-PYTHON-OAUTHLIB-123456](https://security.snyk.io/vuln/SNYK-PYTHON-OAUTHLIB-123456)"
        " — fixed in `4.0.0`"
    )


def test_fixed_vulnerabilities_markdown_without_identifier() -> None:
    """A vulnerability without CVE and without fixed version is rendered with the Snyk identifier only."""
    markdown = _fixed_vulnerabilities_markdown(
        {"package-lock.json": [_vulnerability(cve_ids=[], fixed_in=[])]},
    )

    assert markdown == (
        "## Fixed vulnerabilities\n"
        "\n"
        "- **[HIGH]** `oauthlib` `3.2.2` in `package-lock.json` — "
        "[SNYK-PYTHON-OAUTHLIB-123456](https://security.snyk.io/vuln/SNYK-PYTHON-OAUTHLIB-123456)"
    )


def test_fixed_vulnerabilities_markdown_empty() -> None:
    """Nothing is added to the pull request body when no vulnerability was fixed."""
    assert _fixed_vulnerabilities_markdown({}) == ""


def test_parse_requirements_pins() -> None:
    """The pins added to the requirements files are extracted from the fix git diff."""
    diff = "\n".join(  # noqa: FLY002
        [
            "diff --git a/ci/requirements.txt b/ci/requirements.txt",
            "index 1e413d8abc..b5cdee1d0f 100644",
            "--- a/ci/requirements.txt",
            "+++ b/ci/requirements.txt",
            "@@ -10,3 +10,4 @@",
            "+filelock>=4.2.1 # not directly required, pinned by Snyk to avoid a vulnerability",
            "diff --git a/admin/requirements.txt b/admin/requirements.txt",
            "--- a/admin/requirements.txt",
            "+++ b/admin/requirements.txt",
            "@@ -18,3 +18,4 @@",
            "+Pillow>=13.0.0 # not directly required, pinned by Snyk to avoid a vulnerability",
            "+Setuptools==84.1.0",
            "diff --git a/docker/requirements.txt b/docker/requirements.txt",
            "--- a/docker/requirements.txt",
            "+++ b/docker/requirements.txt",
            "@@ -9,3 +9,3 @@",
            "-dulwich>=0.21.6",
            "+dulwich>=1.2.5",
            "diff --git a/geoportal/package.json b/geoportal/package.json",
            "--- a/geoportal/package.json",
            "+++ b/geoportal/package.json",
            "@@ -1,3 +1,3 @@",
            '+  "lodash": "4.17.21",',
            "diff --git a/old/requirements.txt b/old/requirements.txt",
            "--- a/old/requirements.txt",
            "+++ /dev/null",
            "@@ -1,3 +0,0 @@",
            "-mako==1.3.12",
        ]
    )

    assert audit_utils.parse_requirements_pins(diff) == {
        "ci/requirements.txt": {("filelock", "4.2.1")},
        "admin/requirements.txt": {("pillow", "13.0.0"), ("setuptools", "84.1.0")},
        "docker/requirements.txt": {("dulwich", "1.2.5")},
    }


def test_pin_fixed_vulnerabilities() -> None:
    """The pins are matched to the vulnerabilities by file, package name and fixed version."""
    matched = _vulnerability(
        file_name="ci/requirements.txt",
        package_name="Filelock",
        package_version="3.32.7",
        severity="medium",
        snyk_id="SNYK-PYTHON-FILELOCK-1",
        fixed_in=["4.0.9", "4.2.1"],
    )
    wrong_version = _vulnerability(
        file_name="ci/requirements.txt",
        package_name="virtualenv",
        package_version="20.36.1",
        snyk_id="SNYK-PYTHON-VIRTUALENV-1",
        fixed_in=["21.7.11"],
    )
    other_file = _vulnerability(
        file_name="doc/requirements.txt",
        package_name="filelock",
        package_version="3.32.7",
        snyk_id="SNYK-PYTHON-FILELOCK-2",
        fixed_in=["4.2.1"],
    )
    already_fixed = _vulnerability(
        file_name="admin/requirements.txt",
        package_name="setuptools",
        package_version="79.0.1",
        snyk_id="SNYK-PYTHON-SETUPTOOLS-1",
        fixed_in=["84.1.0"],
    )
    before = {
        "ci/requirements.txt": [matched, wrong_version],
        "doc/requirements.txt": [other_file],
        "admin/requirements.txt": [already_fixed],
    }
    pins = {
        "ci/requirements.txt": {("filelock", "4.2.1"), ("virtualenv", "20.36.2")},
        "doc/requirements.txt": {("dulwich", "1.2.5")},
        "admin/requirements.txt": {("setuptools", "84.1.0")},
    }

    pinned = audit_utils.pin_fixed_vulnerabilities(
        before,
        pins,
        {"admin/requirements.txt": [already_fixed]},
    )

    assert list(pinned) == ["ci/requirements.txt"]
    assert pinned["ci/requirements.txt"] == [
        audit_utils.PinnedVulnerability(vulnerability=matched, pinned_version="4.2.1"),
    ]


def test_pinned_vulnerabilities_markdown() -> None:
    """The pinned vulnerabilities are rendered in their own section of the pull request body."""
    markdown = _pinned_vulnerabilities_markdown(
        {
            "ci/requirements.txt": [
                audit_utils.PinnedVulnerability(
                    vulnerability=_vulnerability(
                        file_name="ci/requirements.txt",
                        package_name="filelock",
                        package_version="3.32.7",
                        severity="medium",
                        snyk_id="SNYK-PYTHON-FILELOCK-1",
                        cve_ids=[],
                        fixed_in=["4.2.1"],
                    ),
                    pinned_version="4.2.1",
                ),
            ],
        },
    )

    assert markdown == (
        "## Fixes applied (pins added to requirements files)\n"
        "\n"
        "The rescan still reports these vulnerabilities because Snyk builds the pip dependency tree\n"
        "from the packages installed in the audit environment, which is not reinstalled after the fix.\n"
        "\n"
        "- **[MEDIUM]** `filelock` `3.32.7` in `ci/requirements.txt` — "
        "[SNYK-PYTHON-FILELOCK-1](https://security.snyk.io/vuln/SNYK-PYTHON-FILELOCK-1)"
        " — fixed in `4.2.1` — pinned to `4.2.1`"
    )


def test_pinned_vulnerabilities_markdown_empty() -> None:
    """Nothing is added to the pull request body when no pin was applied."""
    assert _pinned_vulnerabilities_markdown({}) == ""


def test_details_markdown() -> None:
    """The raw fix output is rendered in a collapsed markdown section."""
    assert _details_markdown("snyk fix output", "Done") == (
        "<details>\n<summary>snyk fix output</summary>\n\nDone\n\n</details>"
    )


def test_vulnerability_status() -> None:
    """The Snyk vulnerability is fully converted to the data stored in the output."""
    status = _vulnerability_status(_vulnerability(), "Not compatible with the used Poetry version")

    assert status.model_dump() == {
        "file": "pyproject.toml",
        "package_name": "oauthlib",
        "package_version": "3.2.2",
        "package_manager": "pip",
        "severity": "high",
        "snyk_id": "SNYK-PYTHON-OAUTHLIB-123456",
        "cve_ids": ["CVE-2026-12345"],
        "cwe_ids": ["CWE-79"],
        "fixed_in": ["4.0.0"],
        "is_upgradable": True,
        "is_patchable": False,
        "reason": "Not compatible with the used Poetry version",
        "id_url": "",
    }


@pytest.mark.asyncio
async def test_process_snyk_pull_request_body() -> None:
    """The Snyk pull request body lists the fixed CVE and links the logs and the generated output."""
    context = Mock()
    context.module_event_data = _EventData(type="snyk", version="1.21")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "tilecloud-chain"
    context.service_url = "https://example.com/"
    context.job_id = 123

    fix_output = module_utils.AnsiMessage("1 items were successfully fixed")
    fix_output.title = "snyk fix output"

    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
            patch.object(
                audit_utils,
                "snyk",
                new=AsyncMock(
                    return_value=(
                        [],
                        fix_output,
                        ["1 high vulnerabilities can be fixed"],
                        True,
                        {},
                        {"pyproject.toml": [_vulnerability()]},
                        {
                            "ci/requirements.txt": [
                                audit_utils.PinnedVulnerability(
                                    vulnerability=_vulnerability(
                                        file_name="ci/requirements.txt",
                                        package_name="filelock",
                                        package_version="3.32.7",
                                        severity="medium",
                                        snyk_id="SNYK-PYTHON-FILELOCK-1",
                                        fixed_in=["4.2.1"],
                                    ),
                                    pinned_version="4.2.1",
                                ),
                            ],
                        },
                    ),
                ),
            ),
            patch.object(audit_utils, "snyk_test_ignored", new=AsyncMock(return_value={})),
            patch.object(audit_utils, "find_snyk_files", new=AsyncMock(return_value=[])),
            patch(
                "github_app_geo_project.module.audit.module_utils.add_output",
                new=AsyncMock(),
            ) as mock_add_output,
        ):
            mock_create_pr.return_value = (True, [], None)

            short_message, success = await _process_snyk_dpkg(
                context,
                module_utils.DashboardIssue("- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"),
                _IntermediateStatus(status=_TransversalStatusRepo()),
            )

    assert success is True
    assert short_message == ["1 high vulnerabilities can be fixed"]

    # The output is created even when every vulnerability was fixed
    mock_add_output.assert_awaited_once()
    assert mock_add_output.await_args is not None
    assert mock_add_output.await_args.args[2] == "snyk-1.21"

    assert mock_create_pr.await_args is not None
    body_md = mock_create_pr.await_args.args[3]
    assert "## Fixed vulnerabilities" in body_md
    assert "[CVE-2026-12345](https://nvd.nist.gov/vuln/detail/CVE-2026-12345)" in body_md
    assert "## Fixes applied (pins added to requirements files)" in body_md
    assert "`filelock` `3.32.7` in `ci/requirements.txt`" in body_md
    assert "pinned to `4.2.1`" in body_md
    assert "<summary>snyk fix output</summary>" in body_md
    assert "1 items were successfully fixed" in body_md
    assert body_md.endswith(
        "[Logs](https://example.com/logs/123) | "
        "[Output](https://example.com/output/camptocamp/tilecloud-chain/snyk-1.21)",
    )


@pytest.mark.asyncio
async def test_process_snyk_pull_request_transversal_status() -> None:
    """The transversal status stores the pull request URL and the summary stays plain text."""
    context = Mock()
    context.module_event_data = _EventData(type="snyk", version="1.21")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "tilecloud-chain"
    context.service_url = "https://example.com/"
    context.job_id = 123

    fix_output = module_utils.AnsiMessage("1 items were successfully fixed")
    fix_output.title = "snyk fix output"

    intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        clone_path.mkdir()
        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
            patch.object(
                audit_utils,
                "snyk",
                new=AsyncMock(
                    return_value=(
                        [],
                        fix_output,
                        ["1 high vulnerabilities can be fixed"],
                        True,
                        {},
                        {"pyproject.toml": [_vulnerability()]},
                        {},
                    ),
                ),
            ),
            patch.object(audit_utils, "snyk_test_ignored", new=AsyncMock(return_value={})),
            patch.object(audit_utils, "find_snyk_files", new=AsyncMock(return_value=[])),
            patch(
                "github_app_geo_project.module.audit.module_utils.add_output",
                new=AsyncMock(),
            ),
        ):
            mock_create_pr.return_value = (
                True,
                ["[Pull request](https://github.com/camptocamp/tilecloud-chain/pull/42)"],
                "https://github.com/camptocamp/tilecloud-chain/pull/42",
            )

            short_message, success = await _process_snyk_dpkg(
                context,
                module_utils.DashboardIssue("- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"),
                intermediate_status,
            )

    assert success is True
    # The markdown link is kept for the check run output summary
    assert short_message == [
        "1 high vulnerabilities can be fixed",
        "[Pull request](https://github.com/camptocamp/tilecloud-chain/pull/42)",
    ]

    tool = intermediate_status.status.types["Snyk check/fix 1.21"]
    assert tool.pull_request_url == "https://github.com/camptocamp/tilecloud-chain/pull/42"
    assert tool.summary == "1 high vulnerabilities can be fixed"


@pytest.mark.asyncio
async def test_process_snyk_branch_ghci_configuration() -> None:
    """The `audit` section of the stabilization branch `.github/ghci.yaml` is passed to the Snyk audit."""
    context = Mock()
    context.module_event_data = _EventData(type="snyk", version="1.21")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "tilecloud-chain"
    context.service_url = "https://example.com/"
    context.job_id = 123

    intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())
    with tempfile.TemporaryDirectory() as tmpdirname:
        clone_path = Path(tmpdirname) / "repo"
        (clone_path / ".github").mkdir(parents=True)
        (clone_path / ".github" / "ghci.yaml").write_text("audit:\n  snyk:\n    auto-fix: false\n")
        mock_cm = _make_worktree_mock(clone_path)
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes",
            ) as mock_create_pr,
            patch.object(
                audit_utils,
                "snyk",
                new=AsyncMock(
                    return_value=([], None, ["1 high vulnerabilities can be fixed"], True, {}, {}, {}),
                ),
            ) as mock_snyk,
            patch.object(audit_utils, "snyk_test_ignored", new=AsyncMock(return_value={})),
            patch.object(audit_utils, "find_snyk_files", new=AsyncMock(return_value=[])),
            patch(
                "github_app_geo_project.module.audit.module_utils.add_output",
                new=AsyncMock(),
            ),
        ):
            mock_create_pr.return_value = (True, [], None)

            _, success = await _process_snyk_dpkg(
                context,
                module_utils.DashboardIssue("- [ ] <!-- snyk --> Check security vulnerabilities with Snyk\n"),
                intermediate_status,
            )

    assert success is True
    assert mock_snyk.await_args is not None
    # The Snyk local configuration of the branch is forwarded to the audit, `auto-fix` included
    assert mock_snyk.await_args.args[6] == {"auto-fix": False}


def test_get_transversal_dashboard_pull_request_url() -> None:
    """The pull request URL is exposed to the dashboard renderer for global and branch types."""
    context = module.TransversalDashboardContext(
        status=_TransversalStatus(
            updated={"owner/repo": dt.datetime.now(dt.UTC)},
            repositories={
                "owner/repo": _TransversalStatusRepo(
                    types={
                        "Cleanup": _TransversalStatusTool(
                            name="Cleanup",
                            summary="Everything is clean",
                            status="success",
                            pull_request_url="https://github.com/owner/repo/pull/1",
                        ),
                        "Snyk check/fix 1.2": _TransversalStatusTool(
                            name="Snyk check/fix 1.2",
                            status="success",
                            pull_request_url="https://github.com/owner/repo/pull/2",
                        ),
                    }
                )
            },
        ),
        params={},
    )

    output = Audit().get_transversal_dashboard(context)

    repository = output.data["repositories"][0]
    assert repository["global_types"][0]["pull_request_url"] == "https://github.com/owner/repo/pull/1"
    assert repository["branches"][0]["types"][0]["pull_request_url"] == "https://github.com/owner/repo/pull/2"


@pytest.mark.asyncio
async def test_dashboard_renders_pull_request_link() -> None:
    """The dashboard renders the pull request URL as a link, not as raw markdown."""
    context = module.TransversalDashboardContext(
        status=_TransversalStatus(
            updated={"owner/repo": dt.datetime.now(dt.UTC)},
            repositories={
                "owner/repo": _TransversalStatusRepo(
                    types={
                        "Cleanup": _TransversalStatusTool(
                            name="Cleanup",
                            summary="Everything is clean",
                            status="success",
                            pull_request_url="https://github.com/owner/repo/pull/1",
                        ),
                        "Snyk check/fix 1.2": _TransversalStatusTool(
                            name="Snyk check/fix 1.2",
                            summary="1 high vulnerabilities can be fixed",
                            status="success",
                            pull_request_url="https://github.com/owner/repo/pull/2",
                        ),
                    }
                )
            },
        ),
        params={},
    )

    output = Audit().get_transversal_dashboard(context)
    html = await render_template(output.renderer, output.data, nonce="the-nonce")

    assert '<a href="https://github.com/owner/repo/pull/1">Pull request</a>' in html
    assert '<a href="https://github.com/owner/repo/pull/2">Pull request</a>' in html
    assert "[Pull request](" not in html


async def _render_audit_output(**vulnerabilities: dict[str, list[_VulnerabilityStatus]]) -> str:
    """Render the Snyk summary report output page."""
    renderer_data = _OutputRendererData(
        branch="1.21",
        vulnerabilities=vulnerabilities.get("vulnerabilities", {}),
        ignored_vulnerabilities=vulnerabilities.get("ignored_vulnerabilities", {}),
        low_severity_vulnerabilities=vulnerabilities.get("low_severity_vulnerabilities", {}),
    )
    return await render_template(
        "github_app_geo_project:module/audit/output.html",
        {"renderer_data": renderer_data.model_dump()},
        nonce="the-nonce",
    )


@pytest.mark.asyncio
async def test_audit_output_without_vulnerability() -> None:
    """The output page reports that no vulnerability was found when everything was fixed."""
    html = await _render_audit_output()

    assert '<style nonce="the-nonce">' in html
    assert "<h2>1.21</h2>" in html
    assert "No vulnerability found." in html


@pytest.mark.asyncio
async def test_audit_output_with_vulnerability() -> None:
    """The output page lists the remaining vulnerabilities without the empty report message."""
    html = await _render_audit_output(
        vulnerabilities={"pyproject.toml": [_vulnerability_status(_vulnerability())]},
    )

    assert "No vulnerability found." not in html
    assert "SNYK-PYTHON-OAUTHLIB-123456" in html


@pytest.mark.asyncio
async def test_find_compatible_java_path(tmp_path: Path) -> None:
    """The newest installed OpenJDK compatible with the Gradle version is selected."""
    jvm_root = tmp_path / "jvm"
    for version in (11, 17, 21, 25):
        bin_dir = jvm_root / f"java-{version}-openjdk-amd64" / "bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "java").write_text("")
    # An installation without the java binary is ignored
    (jvm_root / "java-19-openjdk-amd64" / "bin").mkdir(parents=True)

    jvm_root_path = anyio.Path(str(jvm_root))
    assert await audit_utils.find_compatible_java_path("8.10.2", jvm_root=jvm_root_path) == str(
        jvm_root / "java-21-openjdk-amd64" / "bin"
    )
    assert await audit_utils.find_compatible_java_path("7.6.4", jvm_root=jvm_root_path) == str(
        jvm_root / "java-17-openjdk-amd64" / "bin"
    )
    assert await audit_utils.find_compatible_java_path("6.9.4", jvm_root=jvm_root_path) == str(
        jvm_root / "java-11-openjdk-amd64" / "bin"
    )
    # Recent or unknown Gradle versions run with the system default Java
    assert await audit_utils.find_compatible_java_path("9.1.0", jvm_root=jvm_root_path) is None
    assert await audit_utils.find_compatible_java_path("abc", jvm_root=jvm_root_path) is None
    # Missing JVM root
    assert (
        await audit_utils.find_compatible_java_path("8.10.2", jvm_root=anyio.Path(str(tmp_path / "missing")))
        is None
    )


def _make_gradle_proc_mock(version_output: str) -> MagicMock:
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(version_output.encode(), b""))
    mock_proc.returncode = 0
    return mock_proc


@pytest.mark.asyncio
async def test_select_java_version_fallback(tmp_path: Path) -> None:
    """Without a java-path-for-gradle mapping, a compatible installed Java is selected."""
    (tmp_path / "gradlew").write_text("")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_make_gradle_proc_mock("Gradle 8.10.2\n")),
        ),
        patch.object(
            audit_utils,
            "find_compatible_java_path",
            new=AsyncMock(return_value="/opt/java/21/bin"),
        ) as mock_find,
        patch.object(
            audit_utils.module_utils,
            "run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ) as mock_run_timeout,
    ):
        await audit_utils._select_java_version({}, {}, env, anyio.Path(str(tmp_path)))

    mock_find.assert_awaited_once_with("8.10.2")
    mock_run_timeout.assert_not_awaited()
    assert env["PATH"] == "/opt/java/21/bin:/usr/bin"


@pytest.mark.asyncio
async def test_select_java_version_fallback_not_found(tmp_path: Path) -> None:
    """Without a mapping and without a compatible Java, the environment is left untouched."""
    (tmp_path / "gradlew").write_text("")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_make_gradle_proc_mock("Gradle 8.10.2\n")),
        ),
        patch.object(audit_utils, "find_compatible_java_path", new=AsyncMock(return_value=None)),
        patch.object(
            audit_utils.module_utils,
            "run_timeout",
            new=AsyncMock(return_value=("", True, None)),
        ) as mock_run_timeout,
    ):
        await audit_utils._select_java_version({}, {}, env, anyio.Path(str(tmp_path)))

    mock_run_timeout.assert_awaited_once()
    assert env["PATH"] == "/usr/bin"


@pytest.mark.asyncio
async def test_select_java_version_explicit_mapping(tmp_path: Path) -> None:
    """The explicit java-path-for-gradle mapping takes precedence."""
    (tmp_path / "gradlew").write_text("")
    env = {"PATH": "/usr/bin"}

    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_make_gradle_proc_mock("Gradle 8.10.2\n")),
        ),
        patch.object(audit_utils, "find_compatible_java_path", new=AsyncMock()) as mock_find,
    ):
        await audit_utils._select_java_version(
            {"java-path-for-gradle": {"8.10": "/opt/java/configured/bin"}},
            {},
            env,
            anyio.Path(str(tmp_path)),
        )

    mock_find.assert_not_awaited()
    assert env["PATH"] == "/opt/java/configured/bin:/usr/bin"


def test_is_python_incompatible() -> None:
    """A release requiring a newer Python than the branch is flagged incompatible."""
    assert audit_utils.is_python_incompatible(">=3.11", "3.10") is True
    assert audit_utils.is_python_incompatible(">=3.11", "3.11") is False
    assert audit_utils.is_python_incompatible(">=3.10", "3.10") is False
    assert audit_utils.is_python_incompatible(">=3.9,<4", "3.10") is False
    # No specifier or no branch Python: the pin is kept on a doubt.
    assert audit_utils.is_python_incompatible(None, "3.10") is False
    assert audit_utils.is_python_incompatible("", "3.10") is False
    assert audit_utils.is_python_incompatible(">=3.11", "") is False
    # An unparsable specifier is treated as compatible.
    assert audit_utils.is_python_incompatible("not-a-spec", "3.10") is False


def test_remove_pin_lines() -> None:
    """Only the exact pinned requirement lines are removed, the others are preserved."""
    content = (
        "c2cciutils[checks]==1.5.16\n"
        "Filelock>=4.2.1 # not directly required, pinned by Snyk to avoid a vulnerability\n"
        "setuptools>=84.1.0 # not directly required, pinned by Snyk to avoid a vulnerability\n"
        "filelock>=3.8.0\n"
    )

    new_content, changed = audit_utils.remove_pin_lines(content, {("filelock", "4.2.1")})

    assert changed is True
    assert "Filelock>=4.2.1" not in new_content
    # The other filelock line (another version) and the unrelated pins are kept.
    assert "filelock>=3.8.0\n" in new_content
    assert "setuptools>=84.1.0" in new_content

    unchanged, unchanged_flag = audit_utils.remove_pin_lines(content, {("virtualenv", "21.0.0")})
    assert unchanged_flag is False
    assert unchanged == content


def test_remove_pin_lines_extras() -> None:
    """A requirement with an extras section is matched by its normalized name and version."""
    new_content, changed = audit_utils.remove_pin_lines(
        "virtualenv[foo]>=4.2.1 # pinned\n",
        {("virtualenv", "4.2.1")},
    )

    assert changed is True
    assert new_content == ""


@pytest.mark.asyncio
async def test_drop_python_incompatible_pins(tmp_path: Path) -> None:
    """Pins whose release needs a newer Python are removed, compatible pins are kept."""
    (tmp_path / "ci").mkdir()
    (tmp_path / "ci" / "requirements.txt").write_text(
        "filelock>=4.2.1 # not directly required, pinned by Snyk to avoid a vulnerability\n"
        "setuptools>=84.1.0 # not directly required, pinned by Snyk to avoid a vulnerability\n",
    )
    (tmp_path / "admin").mkdir()
    (tmp_path / "admin" / "requirements.txt").write_text(
        "pillow>=13.0.0 # not directly required, pinned by Snyk to avoid a vulnerability\n",
    )
    pins_by_file = {
        "ci/requirements.txt": {("filelock", "4.2.1"), ("setuptools", "84.1.0")},
        "admin/requirements.txt": {("pillow", "13.0.0")},
    }
    requires_python = {"filelock": ">=3.11", "pillow": ">=3.11", "setuptools": ">=3.10"}

    async def fake_fetch(_session: object, name: str, _version: str) -> str | None:
        return requires_python[name]

    with patch.object(
        audit_utils,
        "_fetch_requires_python",
        new=AsyncMock(side_effect=fake_fetch),
    ):
        dropped = await audit_utils.drop_python_incompatible_pins(
            anyio.Path(str(tmp_path)),
            pins_by_file,
            "3.10",
        )

    assert sorted(dropped) == ["filelock>=4.2.1", "pillow>=13.0.0"]
    ci_content = (tmp_path / "ci" / "requirements.txt").read_text()
    assert "filelock>=4.2.1" not in ci_content
    assert "setuptools>=84.1.0" in ci_content
    assert (tmp_path / "admin" / "requirements.txt").read_text() == ""


@pytest.mark.asyncio
async def test_drop_python_incompatible_pins_without_python_version(tmp_path: Path) -> None:
    """Without a branch Python version nothing is fetched nor dropped."""
    with patch.object(audit_utils, "_fetch_requires_python", new=AsyncMock()) as mock_fetch:
        dropped = await audit_utils.drop_python_incompatible_pins(
            anyio.Path(str(tmp_path)),
            {"ci/requirements.txt": {("filelock", "4.2.1")}},
            "",
        )

    assert dropped == []
    mock_fetch.assert_not_awaited()


# ---------------------------------------------------------------------------
# Trivy container images scan
# ---------------------------------------------------------------------------


def test_extract_images_handles_string_and_object_forms() -> None:
    """`extract_images` finds images given as a string and as a repository/tag/digest object."""
    manifest = {
        "spec": {
            "containers": [
                {"image": "ghcr.io/camptocamp/foo:1.2.3"},
                {"image": {"repository": "docker.io/library/postgres", "tag": "16"}},
                {"image": {"repository": "ghcr.io/bar/app", "digest": "sha256:abc"}},
            ],
            "initContainers": [{"image": "busybox"}],
        },
        # image-less nested content must be ignored safely
        "metadata": {"name": "app"},
    }
    images = audit_utils.extract_images(manifest)
    assert images == {
        "ghcr.io/camptocamp/foo:1.2.3",
        "docker.io/library/postgres:16",
        "ghcr.io/bar/app@sha256:abc",
        "busybox",
    }


def test_parse_trivy_report_reads_vulnerabilities() -> None:
    """`_parse_trivy_report` maps the Trivy JSON to the typed vulnerabilities."""
    report = {
        "Results": [
            {
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2026-0001",
                        "PkgName": "libssl",
                        "InstalledVersion": "3.0.1",
                        "FixedVersion": "3.0.2",
                        "Severity": "CRITICAL",
                        "Title": "Buffer overflow",
                        "References": ["https://nvd.nist.gov/vuln/detail/CVE-2026-0001"],
                    },
                    {
                        "VulnerabilityID": "GHSA-xxxx-yyyy",
                        "PkgName": "openssl",
                        "InstalledVersion": "1.1.1",
                        "Severity": "CRITICAL",
                    },
                ],
            },
        ],
    }
    vulnerabilities = audit_utils._parse_trivy_report(json.dumps(report))
    assert len(vulnerabilities) == 2
    first = vulnerabilities[0]
    assert first.cve == "CVE-2026-0001"
    assert first.package == "libssl"
    assert first.installed_version == "3.0.1"
    assert first.fixed_version == "3.0.2"
    assert first.severity == "critical"
    assert first.references == ["https://nvd.nist.gov/vuln/detail/CVE-2026-0001"]
    # The second entry has no fix yet.
    assert vulnerabilities[1].fixed_version == ""


def test_parse_trivy_report_invalid_json() -> None:
    """A malformed Trivy output yields no vulnerability instead of raising."""
    assert audit_utils._parse_trivy_report("not json") == []


@pytest.mark.asyncio
async def test_discover_applications_structures_and_exclusion(
    tmp_path: Any,
) -> None:
    """The application discovery honours the environments, the auto structure and the exclusions."""
    root = anyio.Path(str(tmp_path))
    await (root / "apps" / "int" / "client" / "app-one").mkdir(parents=True)
    await (root / "apps" / "prod" / "client" / "app-two").mkdir(parents=True)
    await (root / "apps" / "prod" / "client" / "skip-me").mkdir(parents=True)
    await (root / "apps" / "example" / "int" / "test-app").mkdir(parents=True)
    for directory in [
        "int/client/app-one",
        "prod/client/app-two",
        "prod/client/skip-me",
        "example/int/test-app",
    ]:
        await (root / "apps" / directory / "Chart.yaml").write_text("apiVersion: v2\n", encoding="utf-8")
    # A vendored sub-chart (deeper) must be ignored by the auto detection.
    await (root / "apps" / "int" / "client" / "app-one" / "charts" / "dep").mkdir(parents=True)
    await (root / "apps" / "int" / "client" / "app-one" / "charts" / "dep" / "Chart.yaml").write_text(
        "apiVersion: v2\n",
        encoding="utf-8",
    )

    # `auto` + default environments [int, prod]: the two-level apps are found, example is excluded.
    found = await audit_utils.discover_applications(
        root,
        "apps",
        ["int", "prod"],
        "auto",
        [r"skip-me$"],
    )
    assert found == ["apps/int/client/app-one", "apps/prod/client/app-two"]

    # A single-level repository is also detected by `auto`.
    single_root = anyio.Path(str(tmp_path / "single"))
    await (single_root / "apps" / "int" / "fmeflow").mkdir(parents=True)
    await (single_root / "apps" / "int" / "fmeflow" / "Chart.yaml").write_text(
        "apiVersion: v2\n", encoding="utf-8"
    )
    found_single = await audit_utils.discover_applications(single_root, "apps", ["int", "prod"], "auto", [])
    assert found_single == ["apps/int/fmeflow"]


@pytest.mark.asyncio
async def test_application_images_refreshes_repositories(tmp_path: Path) -> None:
    """The Helm dependencies are updated without `--skip-refresh` and the images are extracted."""
    rendered = "apiVersion: v1\nkind: Pod\nspec:\n  containers:\n    - image: ghcr.io/camptocamp/app:1.0\n"

    async def fake_run_timeout(command: list[str], *args: object, **kwargs: object) -> tuple[str, bool, None]:
        if command[:3] == ["helm", "dependency", "update"]:
            return "", True, None
        return rendered, True, None

    with patch.object(
        audit_utils.module_utils,
        "run_timeout",
        new=AsyncMock(side_effect=fake_run_timeout),
    ) as mock_run_timeout:
        images, error = await audit_utils.application_images(anyio.Path(str(tmp_path)), "apps/int/demo/app")

    assert error == ""
    assert images == {"ghcr.io/camptocamp/app:1.0"}
    commands = [call.args[0] for call in mock_run_timeout.await_args_list]
    # `--skip-refresh` breaks the dependency repositories that are not added with `helm repo add`,
    # see https://github.com/helm/helm/issues/11001 and https://github.com/helm/helm/issues/12921.
    assert commands[0] == ["helm", "dependency", "update"]
    assert not any("--skip-refresh" in command for command in commands)


@pytest.mark.asyncio
async def test_application_images_template_failure(tmp_path: Path) -> None:
    """A failed `helm template` returns no image and an error message."""

    async def fake_run_timeout(command: list[str], *args: object, **kwargs: object) -> tuple[str, bool, None]:
        if command[:3] == ["helm", "dependency", "update"]:
            return "", True, None
        return "", False, None

    with patch.object(
        audit_utils.module_utils,
        "run_timeout",
        new=AsyncMock(side_effect=fake_run_timeout),
    ):
        images, error = await audit_utils.application_images(anyio.Path(str(tmp_path)), "apps/int/demo/app")

    assert images == set()
    assert error == "Failed to render the Helm chart of `apps/int/demo/app`"


@pytest.mark.asyncio
async def test_process_trivy_creates_output_and_transversal_entry() -> None:
    """`_process_trivy` stores the images output and the transversal dashboard entry."""
    context = Mock()
    context.module_event_data = _EventData(type="trivy", version="main")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "argocd-gs-gmf-apps"
    context.service_url = "https://example.com/"
    context.job_id = 99

    report = audit_utils.TrivyReport(
        branch="main",
        applications=[
            audit_utils.TrivyApplication(
                application="apps/int/demo/app",
                images=[
                    audit_utils.TrivyImage(
                        image="ghcr.io/camptocamp/app:1.0",
                        critical_count=1,
                        vulnerabilities=[
                            audit_utils.TrivyVulnerability(
                                cve="CVE-2026-0001",
                                package="libssl",
                                installed_version="3.0.1",
                                fixed_version="3.0.2",
                                severity="critical",
                            ),
                        ],
                    ),
                ],
            ),
        ],
        image_count=1,
        critical_total=1,
        scan_errors=0,
    )

    issue_check = module_utils.DashboardIssue("")
    intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())

    with tempfile.TemporaryDirectory() as tmpdirname:
        mock_cm = _make_worktree_mock(Path(tmpdirname))
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch.object(audit_utils, "trivy_images", new=AsyncMock(return_value=(report, []))),
            patch(
                "github_app_geo_project.module.audit.module_utils.add_output",
                new=AsyncMock(),
            ) as mock_add_output,
        ):
            short_message, success = await _process_trivy(
                context,
                issue_check,
                intermediate_status,
            )

    assert success is True
    assert short_message == ["1 applications, 1 images, 1 critical"]

    mock_add_output.assert_awaited_once()
    call = mock_add_output.await_args
    assert call is not None
    assert call.args[2] == "trivy-images-main"
    assert call.args[3] == "github_app_geo_project:module/audit/images.html"

    types = intermediate_status.status.types
    assert "Trivy images main" in types
    entry = types["Trivy images main"]
    assert entry.status == "success"
    assert entry.output_url == "https://example.com/output/camptocamp/argocd-gs-gmf-apps/trivy-images-main"
    assert entry.logs_url == "https://example.com/logs/99"


@pytest.mark.asyncio
async def test_process_fan_out_creates_trivy_action_when_apps_present() -> None:
    """The fan-out adds a `trivy` job for the default branch when an `apps/` directory exists."""
    context = Mock()
    context.module_event_data = _EventData(snyk=False, dpkg=False)
    context.github_event_data = {}
    context.module_config = {"version-mapping": {}}
    context.issue_data = ""
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "argocd-gs-gmf-apps"
    context.github_project.default_branch = AsyncMock(return_value="main")

    async def async_get_content(owner, repo, path):
        if path == "apps":
            return MagicMock(parsed_data=[{"name": "int"}])
        raise _make_request_failed_exception(404)

    context.github_project.aio_github.rest.repos.async_get_content = AsyncMock(side_effect=async_get_content)

    result = await Audit().process(context)

    assert result.intermediate_status is not None
    assert "Trivy images main" in result.intermediate_status.known_types
    trivy_actions = [action for action in result.actions if action.data.type == "trivy"]
    assert len(trivy_actions) == 1
    assert trivy_actions[0].data == _EventData(type="trivy", version="main")


def test_get_transversal_dashboard_repository_filter() -> None:
    """`?repository=` restricts the audit dashboard to a single repository."""
    status = _TransversalStatus(
        updated={
            "camptocamp/argocd-gs-gmf-apps": dt.datetime.now(dt.UTC),
            "camptocamp/other": dt.datetime.now(dt.UTC),
        },
        repositories={
            "camptocamp/argocd-gs-gmf-apps": _TransversalStatusRepo(
                types={"Trivy images main": _TransversalStatusTool(name="Trivy images main")}
            ),
            "camptocamp/other": _TransversalStatusRepo(
                types={"Snyk check/fix 1.2": _TransversalStatusTool(name="Snyk check/fix 1.2")}
            ),
        },
    )
    context = module.TransversalDashboardContext(status=status, params={"repository": "camptocamp/other"})
    output = Audit().get_transversal_dashboard(context)
    repositories = output.data["repositories"]
    assert [repository["repository"] for repository in repositories] == ["camptocamp/other"]

    context_all = module.TransversalDashboardContext(status=status, params={})
    output_all = Audit().get_transversal_dashboard(context_all)
    assert len(output_all.data["repositories"]) == 2


@pytest.mark.asyncio
async def test_render_trivy_images_template() -> None:
    """The images output template renders the applications, images and critical vulnerabilities."""
    report = audit_utils.TrivyReport(
        branch="main",
        applications=[
            audit_utils.TrivyApplication(
                application="apps/int/demo/app",
                images=[
                    audit_utils.TrivyImage(
                        image="ghcr.io/camptocamp/app:1.0",
                        critical_count=1,
                        vulnerabilities=[
                            audit_utils.TrivyVulnerability(
                                cve="CVE-2026-0001",
                                package="libssl",
                                installed_version="3.0.1",
                                fixed_version="3.0.2",
                                severity="critical",
                            ),
                        ],
                    ),
                    audit_utils.TrivyImage(image="ghcr.io/camptocamp/clean:2.0", critical_count=0),
                ],
            ),
        ],
        image_count=2,
        critical_total=1,
        scan_errors=0,
    )
    html = await render_template(
        "github_app_geo_project:module/audit/images.html",
        {"renderer_data": report.model_dump()},
        nonce="test-nonce",
    )
    assert "apps/int/demo/app" in html
    assert "ghcr.io/camptocamp/app:1.0" in html
    assert "CVE-2026-0001" in html
    assert "libssl@3.0.1" in html
    assert "Fixed in: 3.0.2" in html
    assert "no vulnerability" in html


def _dependency_vulnerability(
    file: str = "poetry.lock",
    package: str = "urllib3",
    installed: str = "1.22",
    fixed: str = "2.2.2",
    severity: str = "high",
    vuln_id: str = "CVE-2023-45803",
    dependency_type: str = "poetry",
) -> audit_utils.DependencyVulnerability:
    """Build a Trivy dependency vulnerability for the tests."""
    return audit_utils.DependencyVulnerability(
        file=file,
        package_name=package,
        installed_version=installed,
        dependency_type=dependency_type,
        severity=severity,
        trivy_id=vuln_id,
        fixed_versions=[version.strip() for version in fixed.split(",") if version.strip()],
        cve_ids=[vuln_id] if vuln_id.startswith("CVE-") else [],
        title="Some title",
        id_url=f"https://avd.aquasec.com/nvd/{vuln_id.lower()}",
    )


def test_parse_trivy_fs_report() -> None:
    """Only the Python dependency results are parsed, with the CVE aliases and fix candidates."""
    report = {
        "Results": [
            {
                "Target": "poetry.lock",
                "Type": "poetry",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-2023-45803",
                        "PkgName": "urllib3",
                        "InstalledVersion": "1.22",
                        "FixedVersion": "2.0.6, 1.26.17",
                        "Severity": "HIGH",
                        "Aliases": ["CVE-2023-45803", "GHSA-2xjv-v924-q3f7"],
                        "CweIDs": ["CWE-770"],
                        "Title": "Compression bomb",
                        "PrimaryURL": "https://avd.aquasec.com/nvd/cve-2023-45803",
                    },
                ],
            },
            {
                "Target": "ci/requirements.txt",
                "Type": "pip",
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "SNYK-PYTHON-REQUESTS-1",
                        "PkgName": "requests",
                        "InstalledVersion": "2.19.0",
                        "FixedVersion": "2.20.0",
                        "Severity": "CRITICAL",
                        "Aliases": ["CVE-2018-18074"],
                    },
                ],
            },
            {"Target": "package-lock.json", "Type": "npm", "Vulnerabilities": [{"PkgName": "x"}]},
        ],
    }
    vulnerabilities = audit_utils.parse_trivy_fs_report(json.dumps(report))
    assert len(vulnerabilities) == 2
    urllib3 = vulnerabilities[0]
    assert urllib3.file == "poetry.lock"
    assert urllib3.fixed_versions == ["2.0.6", "1.26.17"]
    assert urllib3.severity == "high"
    assert urllib3.cve_ids == ["CVE-2023-45803"]
    assert urllib3.cwe_ids == ["CWE-770"]
    requests_vuln = vulnerabilities[1]
    assert requests_vuln.file == "ci/requirements.txt"
    assert requests_vuln.cve_ids == ["CVE-2018-18074"]
    assert audit_utils.parse_trivy_fs_report(None) == []
    assert audit_utils.parse_trivy_fs_report("not json") == []


def test_choose_target_version() -> None:
    """The target is the smallest version fixing all the vulnerabilities of the package."""
    assert (
        audit_utils.choose_target_version(
            [
                _dependency_vulnerability(fixed="2.0.6, 1.26.17"),
                _dependency_vulnerability(vuln_id="CVE-2025-66471", fixed="2.6.0"),
            ],
        )
        == "2.6.0"
    )
    assert audit_utils.choose_target_version([_dependency_vulnerability(fixed="1.26.17, 2.0.6")]) == "1.26.17"
    assert audit_utils.choose_target_version([_dependency_vulnerability(fixed="1.10")]) is None
    assert audit_utils.choose_target_version([_dependency_vulnerability(fixed="")]) is None
    assert audit_utils.choose_target_version([_dependency_vulnerability(installed="not-a-version")]) is None


def test_build_dependency_fix_plans() -> None:
    """Plans group the vulnerabilities per file and package, below the threshold or unsupported are skipped."""
    vulnerabilities = [
        _dependency_vulnerability(),
        _dependency_vulnerability(vuln_id="CVE-2024-1", fixed="2.6.0"),
        _dependency_vulnerability(severity="medium", vuln_id="CVE-2024-2", fixed="2.7.1"),
        _dependency_vulnerability(
            file="requirements.txt",
            package="django",
            installed="3.2",
            fixed="4.0",
            dependency_type="pip",
        ),
        _dependency_vulnerability(
            file="Pipfile.lock",
            package="pyyaml",
            installed="5.0",
            fixed="6.0",
            severity="critical",
            dependency_type="pipenv",
        ),
        _dependency_vulnerability(file="ci/requirements.txt", package="black", fixed="24.0"),
    ]
    plans, skipped = audit_utils.build_dependency_fix_plans(vulnerabilities, "high", ["ci/requirements.txt"])
    assert [(plan.file, plan.package_name, plan.target_version) for plan in plans] == [
        ("poetry.lock", "urllib3", "2.6.0"),
        ("requirements.txt", "django", "4.0"),
    ]
    assert any("Pipfile.lock" in reason and "unsupported" in reason for reason in skipped)
    assert any("black" in reason and "files-no-fix" in reason for reason in skipped)


def test_find_and_apply_temp_poetry_constraint_classic() -> None:
    """The classical `[tool.poetry.dependencies]` entries are found and replaced."""
    content = """
[tool.poetry]
name = "p"

[tool.poetry.dependencies]
python = ">=3.10"
urllib3 = ">=1.26,<2"
requests = { version = ">=2.0", extras = ["security"] }

[tool.poetry.group.dev.dependencies]
black = ">=23"
"""
    assert audit_utils.find_pyproject_constraint(content, "urllib3") == "urllib3 >=1.26,<2"
    assert audit_utils.find_pyproject_constraint(content, "python") == "python >=3.10"
    assert audit_utils.find_pyproject_constraint(content, "black") == "black >=23"
    assert audit_utils.find_pyproject_constraint(content, "pyyaml") is None
    modified = audit_utils.apply_temp_poetry_constraint(content, "urllib3", "2.2.2")
    assert 'urllib3 = "==2.2.2"' in modified
    assert ">=1.26,<2" not in modified
    assert "[tool.poetry.dependencies]" in modified
    assert 'python = ">=3.10"' in modified
    injected = audit_utils.apply_temp_poetry_constraint(content, "pyyaml", "6.0.1")
    assert 'pyyaml = "==6.0.1"' in injected
    assert injected.count("[tool.poetry.dependencies]") == 1
    assert "[tool.poetry.group.dev.dependencies]" in injected


def test_find_and_apply_temp_poetry_constraint_pep621() -> None:
    """The PEP 621 `dependencies` array entries are found, replaced and injected."""
    content = """
[project]
name = "p"
dependencies = [
    "requests>=2.19.0",
    "urllib3 >=1.26,<3",  # bounded
]

[tool.poetry]
package-mode = false
"""
    assert audit_utils.find_pyproject_constraint(content, "requests") == "requests >=2.19.0"
    assert audit_utils.find_pyproject_constraint(content, "urllib3") == "urllib3 >=1.26,<3"
    assert audit_utils.find_pyproject_constraint(content, "pyyaml") is None
    modified = audit_utils.apply_temp_poetry_constraint(content, "requests", "2.31.0")
    assert '"requests==2.31.0"' in modified
    assert ">=2.19.0" not in modified
    assert "# bounded" in modified
    injected = audit_utils.apply_temp_poetry_constraint(content, "pyyaml", "6.0.1")
    assert '"pyyaml==6.0.1",' in injected


def test_locked_poetry_version() -> None:
    """The locked version of a package is extracted from a poetry.lock content."""
    content = """
[[package]]
name = "requests"
version = "2.31.0"
description = "x"

[[package]]
name = "urllib3"
version = "2.2.2"
description = "y"
"""
    assert audit_utils.locked_poetry_version(content, "urllib3") == "2.2.2"
    assert audit_utils.locked_poetry_version(content, "pyyaml") is None


def test_bump_requirement_content() -> None:
    """Pins are bumped in place, missing packages get a floor, bounded constraints are skipped."""
    content = "# header\nurllib3==1.22  # http\nDjango>=3.2,<4.0\nblack==24.0.0\n"

    new_content, outcome = audit_utils.bump_requirement_content(content, "urllib3", "2.2.2")
    assert outcome == "bumped"
    assert "urllib3==2.2.2  # http" in new_content

    new_content, outcome = audit_utils.bump_requirement_content(content, "requests", "2.31.0")
    assert outcome == "appended"
    assert new_content.endswith("black==24.0.0\nrequests>=2.31.0\n")

    _, outcome = audit_utils.bump_requirement_content(content, "django", "4.2")
    assert outcome.startswith("skip:")

    _, outcome = audit_utils.bump_requirement_content(content, "black", "24.0.0")
    assert outcome == "already"

    _, outcome = audit_utils.bump_requirement_content(content, "urllib3", "1.21")
    assert outcome == "already"


@pytest.mark.asyncio
async def test_filter_python_incompatible_plans() -> None:
    """Target versions requiring a newer Python than the branch are dropped from the plans."""
    plans = [
        audit_utils.DependencyFixPlan(
            file="poetry.lock",
            package_name="urllib3",
            installed_version="1.22",
            target_version="2.6.0",
            vulnerabilities=[_dependency_vulnerability(fixed="2.6.0")],
        ),
        audit_utils.DependencyFixPlan(
            file="poetry.lock",
            package_name="black",
            installed_version="22.0",
            target_version="24.0.0",
            vulnerabilities=[_dependency_vulnerability(package="black", fixed="24.0.0")],
        ),
    ]

    async def fake_fetch(session: Any, name: str, version: str) -> str:
        return ">=3.10" if name == "black" else ""

    with patch.object(
        audit_utils,
        "_fetch_requires_python",
        new=AsyncMock(side_effect=fake_fetch),
    ):
        kept, skipped = await audit_utils.filter_python_incompatible_plans(plans, "3.9")
    assert [plan.package_name for plan in kept] == ["urllib3"]
    assert len(skipped) == 1
    assert "black" in skipped[0]
    assert "Python 3.9" in skipped[0]

    kept, skipped = await audit_utils.filter_python_incompatible_plans(plans, "")
    assert len(kept) == 2
    assert not skipped


def _make_poetry_run_timeout(lock_success: bool = True) -> AsyncMock:
    """Build a `run_timeout` mock simulating the conservative Poetry lock behaviour."""

    async def fake_run(
        command, env, timeout, success_message, error_message, timeout_message, cwd, error=True
    ):
        directory = Path(cwd)
        if command == ["poetry", "lock"]:
            if not lock_success:
                return "version solving failed", False, None
            pyproject = (directory / "pyproject.toml").read_text(encoding="utf-8")
            match = re.search(r'(\n[a-zA-Z0-9._-]+) = "==([^"]+)"', pyproject)
            if match:
                lock = directory / "poetry.lock"
                content = lock.read_text(encoding="utf-8")
                name = match[1][1:].lstrip()
                content = re.sub(
                    rf'name = "{name}"\nversion = "[^"]+"',
                    f'name = "{name}"\nversion = "{match[2]}"',
                    content,
                )
                lock.write_text(content, encoding="utf-8")
            return "", True, None
        if command == ["poetry", "check", "--lock"]:
            return "", True, None
        msg = f"Unexpected command {command}"
        raise AssertionError(msg)

    return AsyncMock(side_effect=fake_run)


@pytest.mark.asyncio
async def test_fix_poetry_project(tmp_path: Path) -> None:
    """The lock is bumped to the target version and the pyproject.toml is left untouched."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject_content = (
        '[tool.poetry]\nname = "p"\n\n[tool.poetry.dependencies]\npython = ">=3.10"\nurllib3 = ">=1.26"\n'
    )
    pyproject.write_text(pyproject_content, encoding="utf-8")
    lock = tmp_path / "poetry.lock"
    lock_content = '[[package]]\nname = "urllib3"\nversion = "1.22"\ndescription = "HTTP library"\n'
    lock.write_text(lock_content, encoding="utf-8")

    plan = audit_utils.DependencyFixPlan(
        file="poetry.lock",
        package_name="urllib3",
        installed_version="1.22",
        target_version="2.2.2",
        vulnerabilities=[_dependency_vulnerability()],
    )
    with patch.object(audit_utils.module_utils, "run_timeout", new=_make_poetry_run_timeout()):
        applied, skipped, _logs = await audit_utils._fix_poetry_project(
            anyio.Path(str(tmp_path)), "", [plan], os.environ.copy()
        )
    assert applied == ["urllib3 1.22 -> 2.2.2 in `poetry.lock`"]
    assert not skipped
    assert pyproject.read_text(encoding="utf-8") == pyproject_content
    assert 'version = "2.2.2"' in lock.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fix_poetry_project_version_solving_failed(tmp_path: Path) -> None:
    """A package that cannot be resolved to the fixed version is skipped and the files reverted."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject_content = '[tool.poetry.dependencies]\npython = ">=3.10"\n'
    pyproject.write_text(pyproject_content, encoding="utf-8")
    lock = tmp_path / "poetry.lock"
    lock_content = '[[package]]\nname = "urllib3"\nversion = "1.22"\n'
    lock.write_text(lock_content, encoding="utf-8")

    plan = audit_utils.DependencyFixPlan(
        file="poetry.lock",
        package_name="urllib3",
        installed_version="1.22",
        target_version="2.2.2",
        vulnerabilities=[_dependency_vulnerability()],
    )
    with patch.object(
        audit_utils.module_utils, "run_timeout", new=_make_poetry_run_timeout(lock_success=False)
    ):
        applied, skipped, _logs = await audit_utils._fix_poetry_project(
            anyio.Path(str(tmp_path)), "", [plan], os.environ.copy()
        )
    assert applied == []
    assert "version solving failed" in skipped[0]
    assert pyproject.read_text(encoding="utf-8") == pyproject_content
    assert lock.read_text(encoding="utf-8") == lock_content


@pytest.mark.asyncio
async def test_fix_poetry_project_constraint_too_strict(tmp_path: Path) -> None:
    """A direct dependency whose constraint forbids the fixed version is left to the dependency updates."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject_content = '[tool.poetry.dependencies]\nurllib3 = ">=1.22,<2"\n'
    pyproject.write_text(pyproject_content, encoding="utf-8")
    (tmp_path / "poetry.lock").write_text(
        '[[package]]\nname = "urllib3"\nversion = "1.22"\n', encoding="utf-8"
    )

    plan = audit_utils.DependencyFixPlan(
        file="poetry.lock",
        package_name="urllib3",
        installed_version="1.22",
        target_version="2.2.2",
        vulnerabilities=[_dependency_vulnerability()],
    )
    with patch.object(audit_utils.module_utils, "run_timeout", new=_make_poetry_run_timeout()):
        applied, skipped, _logs = await audit_utils._fix_poetry_project(
            anyio.Path(str(tmp_path)), "", [plan], os.environ.copy()
        )
    assert applied == []
    assert "does not allow it" in skipped[0]


@pytest.mark.asyncio
async def test_fix_requirements_files(tmp_path: Path) -> None:
    """The requirements files are edited and validated, a failing resolution is reverted."""
    requirements = tmp_path / "requirements.txt"
    original = "requests==2.19.0\n"
    requirements.write_text(original, encoding="utf-8")

    plans = [
        audit_utils.DependencyFixPlan(
            file="requirements.txt",
            package_name="requests",
            installed_version="2.19.0",
            target_version="2.31.0",
            vulnerabilities=[
                _dependency_vulnerability(
                    file="requirements.txt",
                    package="requests",
                    installed="2.19.0",
                    fixed="2.31.0",
                    dependency_type="pip",
                ),
            ],
        ),
        audit_utils.DependencyFixPlan(
            file="requirements.txt",
            package_name="urllib3",
            installed_version="1.22",
            target_version="2.2.2",
            vulnerabilities=[
                _dependency_vulnerability(
                    file="requirements.txt", installed="1.22", fixed="2.2.2", dependency_type="pip"
                ),
            ],
        ),
    ]

    run_mock = AsyncMock(return_value=("", True, None))
    with patch.object(audit_utils.module_utils, "run_timeout", new=run_mock):
        applied, skipped, pinned = await audit_utils._fix_requirements_files(
            anyio.Path(str(tmp_path)), plans, os.environ.copy(), ["--only-binary=:all:"]
        )
    assert not skipped
    assert any("requests 2.19.0 -> 2.31.0" in message for message in applied)
    assert any("urllib3 pinned" in message for message in applied)
    assert [item.pinned_version for item in pinned] == ["2.2.2"]
    content = requirements.read_text(encoding="utf-8")
    assert "requests==2.31.0" in content
    assert "urllib3>=2.2.2" in content
    assert run_mock.await_args_list[0].args[0][:5] == ["python", "-m", "pip", "install", "--dry-run"]
    assert "--only-binary=:all:" in run_mock.await_args_list[0].args[0]

    requirements.write_text(original, encoding="utf-8")
    with patch.object(
        audit_utils.module_utils,
        "run_timeout",
        new=AsyncMock(return_value=("ResolutionImpossible", False, None)),
    ):
        _applied, skipped, _pinned = await audit_utils._fix_requirements_files(
            anyio.Path(str(tmp_path)), plans, os.environ.copy(), []
        )
    assert "reverted" in skipped[0]
    assert requirements.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_trivy_filesystem_flow(tmp_path: Path) -> None:
    """The dependencies audit fixes the lock, rescans and reports the fixed vulnerabilities."""
    before = [
        _dependency_vulnerability(),
        _dependency_vulnerability(severity="medium", vuln_id="CVE-MED", fixed="1.26.5"),
    ]
    with (
        patch.object(
            audit_utils,
            "_trivy_fs_scan",
            new=AsyncMock(side_effect=[(before, True), ([], True)]),
        ),
        patch.object(
            audit_utils,
            "_fix_poetry_project",
            new=AsyncMock(
                return_value=(["urllib3 1.22 -> 2.2.2 in `poetry.lock`"], [], ["poetry lock log"]),
            ),
        ) as mock_fix,
    ):
        result = await audit_utils.trivy_filesystem(
            "1.0",
            {},
            {},
            os.environ.copy(),
            anyio.Path(str(tmp_path)),
            "",
        )
    assert result.success
    mock_fix.assert_awaited_once()
    assert [vuln.snyk_id for vulns in result.fixed_vulnerabilities.values() for vuln in vulns] == [
        "CVE-2023-45803",
        "CVE-MED",
    ]
    assert result.vulnerabilities == {}
    assert result.skipped_fixes == []
    fix_html = html_module.unescape(result.fix_message.html)
    assert "Applied: urllib3 1.22 -> 2.2.2" in fix_html
    assert "poetry lock log" in fix_html
    assert "1 high vulnerabilities" in result.short_messages
    assert "2 vulnerabilities fixed" in result.short_messages


@pytest.mark.asyncio
async def test_trivy_filesystem_auto_fix_disabled(tmp_path: Path) -> None:
    """With the automatic fixes disabled no file is touched and the vulnerabilities are reported."""
    before = [_dependency_vulnerability()]
    with (
        patch.object(
            audit_utils,
            "_trivy_fs_scan",
            new=AsyncMock(side_effect=[(before, True)]),
        ),
        patch.object(audit_utils, "_fix_poetry_project", new=AsyncMock()) as mock_fix,
    ):
        result = await audit_utils.trivy_filesystem(
            "1.0",
            {"auto-fix": False},
            {},
            os.environ.copy(),
            anyio.Path(str(tmp_path)),
            "",
        )
    mock_fix.assert_not_awaited()
    assert result.success
    assert result.fixed_vulnerabilities == {}
    assert "auto-fix" in result.fix_message.html
    assert len(result.vulnerabilities["poetry.lock"]) == 1


@pytest.mark.asyncio
async def test_trivy_filesystem_scan_failure(tmp_path: Path) -> None:
    """A scan failure is reported as an error of the job."""
    with patch.object(audit_utils, "_trivy_fs_scan", new=AsyncMock(return_value=([], False))):
        result = await audit_utils.trivy_filesystem(
            "1.0",
            {},
            {},
            os.environ.copy(),
            anyio.Path(str(tmp_path)),
            "",
        )
    assert not result.success
    assert result.short_messages == ["Error while scanning the dependencies"]


@pytest.mark.asyncio
async def test_parse_trivyignore_reasons(tmp_path: Path) -> None:
    """The reasons and expiries of the .trivyignore.yaml entries are extracted."""
    ignore_file = tmp_path / ".trivyignore.yaml"
    ignore_file.write_text(
        "vulnerabilities:\n"
        "  - id: CVE-2023-45803\n"
        "    reason: Not compatible with the used Python version\n"
        "    expire: 2026-12-31\n"
        "  - id: CVE-2024-1234\n",
        encoding="utf-8",
    )
    reasons = await audit_utils.parse_trivyignore_reasons(anyio.Path(str(ignore_file)))
    assert reasons["CVE-2023-45803"] == "Not compatible with the used Python version, until 2026-12-31"
    assert reasons["CVE-2024-1234"] == ""


@pytest.mark.asyncio
async def test_trivy_filesystem_output_rendering() -> None:
    """The summary report output renders the Trivy vulnerability IDs with their own URL."""
    data = _OutputRendererData(
        branch="1.2",
        vulnerabilities={
            "poetry.lock": [
                _vulnerability_status(
                    audit_utils.dependency_to_vulnerability_data(_dependency_vulnerability()),
                ),
            ],
        },
        ignored_vulnerabilities={},
        low_severity_vulnerabilities={},
    )
    html = await render_template(
        "github_app_geo_project:module/audit/output.html",
        {"renderer_data": data.model_dump()},
        nonce="test-nonce",
    )
    assert "urllib3@1.22" in html
    assert "https://avd.aquasec.com/nvd/cve-2023-45803" in html
    assert "security.snyk.io" not in html


@pytest.mark.asyncio
async def test_process_fan_out_trivy_filesystem_jobs() -> None:
    """The fan-out creates one `trivy-filesystem` job per supported version."""
    context = Mock()
    context.module_event_data = _EventData(trivy_filesystem=True)
    context.github_event_data = {}
    context.module_config = {"version-mapping": {}}
    context.issue_data = "- [ ] <!-- outdated --> Check outdated version\n"
    context.github_project = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.github_project.default_branch = AsyncMock(return_value="master")

    security_file = MagicMock(spec=githubkit_schemas.latest.models.ContentFile)
    security_file.content = base64.b64encode(b"# Security policy").decode("utf-8")

    async def async_get_content(owner, repo, path):
        if path == "SECURITY.md":
            return MagicMock(parsed_data=security_file)
        raise _make_request_failed_exception(404)

    context.github_project.aio_github.rest.repos.async_get_content = AsyncMock(side_effect=async_get_content)

    with patch("github_app_geo_project.module.audit.security_md.Security") as mock_security:
        mock_security.return_value.branches.return_value = ["1.2", "1.3"]
        result = await Audit().process(context)

    actions = [action for action in result.actions if action.data.type == "trivy-filesystem"]
    assert [action.data.version for action in actions] == ["1.2", "1.3"]
    assert [action.priority for action in actions] == [
        _PRIORITY_TRIVY_FILESYSTEM,
        _PRIORITY_TRIVY_FILESYSTEM,
    ]
    assert not [action for action in result.actions if action.data.type == "snyk"]
    assert "<!-- trivy-filesystem -->" in result.dashboard


@pytest.mark.asyncio
async def test_process_trivy_filesystem_flow() -> None:
    """`_process_trivy_filesystem` renders the pull request body, the output and the transversal entry."""
    context = Mock()
    context.module_event_data = _EventData(type="trivy-filesystem", version="1.2")
    context.module_config = {}
    context.github_project = Mock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repo"
    context.service_url = "https://example.com/"
    context.job_id = 42

    remaining = audit_utils.dependency_to_vulnerability_data(_dependency_vulnerability())
    fixed = audit_utils.dependency_to_vulnerability_data(
        _dependency_vulnerability(
            vuln_id="CVE-2018-18074", package="requests", installed="2.19.0", fixed="2.20.0"
        ),
    )
    ignored_vulnerability = audit_utils.DependencyVulnerability(
        file="requirements.txt",
        package_name="pyyaml",
        installed_version="5.0",
        dependency_type="pip",
        severity="critical",
        trivy_id="CVE-2099-9999",
        fixed_versions=["6.0"],
    )
    fix_message = module_utils.HtmlMessage("Applied: requests 2.19.0 -&gt; 2.20.0")
    fix_message.title = "Trivy dependencies fix output"
    audit_result = audit_utils.TrivyFilesystemResult(
        messages=[],
        fix_message=fix_message,
        short_messages=["1 high vulnerabilities", "1 vulnerability fixed", "1 fixes skipped"],
        success=True,
        vulnerabilities={"poetry.lock": [remaining]},
        fixed_vulnerabilities={"requirements.txt": [fixed]},
        pinned_vulnerabilities={},
        skipped_fixes=["pyyaml in `Pipfile.lock`: not auto-fixable, unsupported file type"],
    )

    issue_check = module_utils.DashboardIssue("")
    intermediate_status = _IntermediateStatus(status=_TransversalStatusRepo())

    with tempfile.TemporaryDirectory() as tmpdirname:
        mock_cm = _make_worktree_mock(Path(tmpdirname))
        with (
            patch(
                "github_app_geo_project.module.audit.module_utils.GIT_WORKTREE_CACHE.working_tree",
                return_value=mock_cm,
            ),
            patch.object(audit_utils, "trivy_filesystem", new=AsyncMock(return_value=audit_result)),
            patch.object(
                audit_utils,
                "trivy_filesystem_ignored",
                new=AsyncMock(
                    return_value={
                        "poetry.lock": [remaining],
                        "requirements.txt": [
                            audit_utils.dependency_to_vulnerability_data(ignored_vulnerability),
                        ],
                    },
                ),
            ),
            patch.object(audit_utils, "find_trivyignore_files", new=AsyncMock(return_value=[])),
            patch(
                "github_app_geo_project.module.audit.module_utils.add_output",
                new=AsyncMock(),
            ) as mock_add_output,
            patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr,
        ):
            mock_create_pr.return_value = (
                True,
                ["[Pull request](https://github.com/owner/repo/pull/1)"],
                "https://github.com/owner/repo/pull/1",
            )

            short_message, success = await _process_trivy_filesystem(
                context,
                issue_check,
                intermediate_status,
            )

    assert success is True
    assert short_message[:3] == [
        "1 high vulnerabilities",
        "1 vulnerability fixed",
        "1 fixes skipped",
    ]

    mock_add_output.assert_awaited_once()
    call = mock_add_output.await_args
    assert call is not None
    assert call.args[2] == "trivy-filesystem-1.2"
    renderer_data = call.kwargs["renderer_data"]
    assert renderer_data.vulnerabilities["poetry.lock"][0].snyk_id == "CVE-2023-45803"
    assert renderer_data.ignored_vulnerabilities["requirements.txt"][0].snyk_id == "CVE-2099-9999"

    mock_create_pr.assert_awaited_once()
    args = mock_create_pr.await_args.args
    assert args[0] == "1.2"
    assert args[1] == "ghci/audit/trivy-filesystem/1.2"
    assert args[2] == "Trivy filesystem check/fix 1.2"
    body = args[3]
    assert "## Fixed vulnerabilities" in body
    assert "requests" in body
    assert "## Fixes not applied" in body
    assert "unsupported file type" in body
    assert "Applied: requests 2.19.0 -> 2.20.0" in body
    assert "[Logs](https://example.com/logs/42)" in body
    assert "[Output](https://example.com/output/owner/repo/trivy-filesystem-1.2)" in body

    types = intermediate_status.status.types
    assert "Trivy filesystem check/fix 1.2" in types
    entry = types["Trivy filesystem check/fix 1.2"]
    assert entry.status == "success"
    assert entry.output_url == "https://example.com/output/owner/repo/trivy-filesystem-1.2"
    assert entry.pull_request_url == "https://github.com/owner/repo/pull/1"
