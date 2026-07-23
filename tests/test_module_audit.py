"""Tests for the audit module."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from github_app_geo_project.module.audit import Audit, _EventData, _process_renovate
from github_app_geo_project.module.audit.utils import VulnerabilityData


@pytest.mark.asyncio
async def test_process_renovate_default_branch_success():
    """Test successful Renovate update on default branch."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123

    known_versions = ["1.0", "2.0"]
    # Mock git_clone to return a valid path
    with (
        patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone,
        tempfile.TemporaryDirectory() as tmpdir,
    ):
        clone_path = Path(tmpdir) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        # EditRenovateConfigV2 expects the first line to be "{"
        renovate_file.write_text("{\n}")

        mock_git_clone.return_value = clone_path

        # Mock EditRenovateConfigV2 to avoid pre-commit issues
        with patch("github_app_geo_project.module.audit.editor.EditRenovateConfig") as mock_editor:
            mock_config = MagicMock()
            mock_editor.return_value = MagicMock(
                __aenter__=AsyncMock(return_value=mock_config),
                __aexit__=AsyncMock(return_value=None),
            )

            # Mock _create_pull_request_if_changes
            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (True, [])

                # Call the function
                result = await _process_renovate(context, known_versions)

                # Assertions
                assert result is True
                # Verify git_clone was called with correct arguments
                assert mock_git_clone.call_count == 1
                call_args = mock_git_clone.call_args
                assert call_args[0][0] == context.github_project
                assert call_args[0][1] == "master"
                # Don't check exact path since function creates its own temp dir

                # Verify the renovate config was updated
                mock_config.__setitem__.assert_called_once_with(
                    "baseBranchPatterns", ["master", "1.0", "2.0"]
                )

                mock_create_pr.assert_called_once()

                # Verify the call arguments
                pr_call_args = mock_create_pr.call_args
                assert pr_call_args[0][0] == "master"  # branch
                assert pr_call_args[0][1] == "ghci/audit/renovate/master"  # new_branch
                assert pr_call_args[0][2] == "Update Renovate configuration"  # key


@pytest.mark.asyncio
async def test_process_renovate_default_branch_no_security_file():
    """Test Renovate update when SECURITY.md is missing."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123

    known_versions = []
    # Mock git_clone to return a valid path
    with (
        patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone,
        tempfile.TemporaryDirectory() as tmpdir,
    ):
        clone_path = Path(tmpdir) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{\n}")

        mock_git_clone.return_value = clone_path

        # Mock EditRenovateConfigV2 to avoid pre-commit issues
        with patch("github_app_geo_project.module.audit.editor.EditRenovateConfig") as mock_editor:
            mock_config = MagicMock()
            mock_config.__contains__.return_value = True
            mock_editor.return_value = MagicMock(
                __aenter__=AsyncMock(return_value=mock_config),
                __aexit__=AsyncMock(return_value=None),
            )

            # Mock _create_pull_request_if_changes
            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (True, [])

                # Call the function
                result = await _process_renovate(context, known_versions)

                # Assertions
                assert result is True
                mock_config.__setitem__.assert_not_called()
                mock_config.__delitem__.assert_called_once_with("baseBranchPatterns")
                mock_create_pr.assert_called_once()


@pytest.mark.asyncio
async def test_process_renovate_default_branch_clone_failure():
    """Test failed clone on default branch scenario."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="master")

    known_versions = ["1.0", "2.0"]
    # Mock git_clone to return None (failure)
    with patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone:
        mock_git_clone.return_value = None

        # Call the function
        result = await _process_renovate(context, known_versions)

        # Assertions
        assert result is False


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_success():
    """Test successful version cleanup scenario."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    # Mock git_clone to return a valid path
    with (
        patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone,
        tempfile.TemporaryDirectory() as tmpdir,
    ):
        clone_path = Path(tmpdir) / "repo"
        clone_path.mkdir()
        github_dir = clone_path / ".github"
        github_dir.mkdir()
        renovate_file = github_dir / "renovate.json5"
        renovate_file.write_text("{}")
        security_file = clone_path / "SECURITY.md"
        security_file.write_text("# Security")

        mock_git_clone.return_value = clone_path

        # Mock _create_pull_request_if_changes
        with patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr:
            mock_create_pr.return_value = (True, [])

            # Call the function
            result = await _process_renovate(context, None)

            # Assertions
            assert result is True
            # Verify git_clone was called with correct arguments
            assert mock_git_clone.call_count == 1
            call_args = mock_git_clone.call_args
            assert call_args[0][0] == context.github_project
            assert call_args[0][1] == "1.0"
            # Don't check exact path since function creates its own temp dir

            # Verify files were deleted
            assert not renovate_file.exists()
            assert not security_file.exists()

            # Verify PR was created
            mock_create_pr.assert_called_once()
            pr_call_args = mock_create_pr.call_args
            assert pr_call_args[0][0] == "1.0"  # branch
            assert pr_call_args[0][1] == "ghci/audit/renovate/1.0"  # new_branch


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_clone_failure():
    """Test failed clone on version cleanup scenario."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    # Mock git_clone to return None (failure)
    with patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone:
        mock_git_clone.return_value = None

        # Call the function
        result = await _process_renovate(context, None)

        # Assertions
        assert result is False


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_files_not_exist():
    """Test version cleanup when files don't exist."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    # Mock git_clone to return a valid path with no renovate or security files
    with (
        patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone,
        tempfile.TemporaryDirectory() as tmpdir,
    ):
        clone_path = Path(tmpdir) / "repo"
        clone_path.mkdir()

        mock_git_clone.return_value = clone_path

        # Mock _create_pull_request_if_changes
        with patch("github_app_geo_project.module.audit._create_pull_request_if_changes") as mock_create_pr:
            mock_create_pr.return_value = (True, [])

            # Call the function
            result = await _process_renovate(context, None)

            # Assertions
            assert result is True
            # Verify git_clone was called with correct arguments
            assert mock_git_clone.call_count == 1
            call_args = mock_git_clone.call_args
            assert call_args[0][0] == context.github_project
            assert call_args[0][1] == "1.0"
            # Don't check exact path since function creates its own temp dir

            # Verify PR was still created (even though no files to delete)
            mock_create_pr.assert_called_once()


@pytest.mark.asyncio
async def test_process_renovate_version_cleanup_pr_creation_failure():
    """Test version cleanup when PR creation fails."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version="1.0")
    context.github_project = Mock()
    context.github_project.default_branch = AsyncMock(return_value="master")
    context.service_url = "https://example.com/"
    context.job_id = 123
    # Mock git_clone to return a valid path
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_path = Path(tmpdir)

        # Mock git_clone to return a valid path
        with patch("github_app_geo_project.module.audit.module_utils.git_clone") as mock_git_clone:
            clone_path = temp_path / "repo"
            clone_path.mkdir()

            mock_git_clone.return_value = clone_path

            # Mock _create_pull_request_if_changes to return failure
            with patch(
                "github_app_geo_project.module.audit._create_pull_request_if_changes"
            ) as mock_create_pr:
                mock_create_pr.return_value = (False, ["Error creating PR"])

                # Call the function
                result = await _process_renovate(context, None)

                # Assertions
                assert result is False


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
    event_data.pull_request.merged = False
    event_data.pull_request.base = Mock()
    event_data.pull_request.base.ref = "master"

    with patch("githubkit.webhooks.parse_obj", return_value=event_data):
        actions = Audit().get_actions(context)

    assert len(actions) == 1
    assert actions[0].data == _EventData(type="close-pull-request-issues")


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

    # Default value when no config is set
    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "medium"

    # Value from global config
    config["dashboard-severity-threshold"] = "high"
    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "high"

    # Local config overrides global
    local_config["dashboard-severity-threshold"] = "critical"
    result = get_severity_config(config, local_config, "dashboard-severity-threshold", "medium")
    assert result == "critical"


def test_get_excluded_files() -> None:
    """Test excluded files config retrieval."""
    from github_app_geo_project.module.audit.utils import get_excluded_files

    config: dict = {}
    local_config: dict = {}

    # Default empty
    result = get_excluded_files(config, local_config)
    assert result == []

    # Global config
    config["excluded-files"] = [r"dev-.*\.txt"]
    result = get_excluded_files(config, local_config)
    assert result == [r"dev-.*\.txt"]

    # Local overrides global
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
