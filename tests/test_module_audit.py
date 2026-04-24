"""Tests for the audit module."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from github_app_geo_project.module.audit import Audit, _EventData, _process_renovate


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
    """Test that merged pull request on default branch triggers Renovate action."""
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

    assert len(actions) == 2
    assert actions[0].data == _EventData(type="close-pull-request-issues")
    assert actions[1].data == _EventData(type="renovate")


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
