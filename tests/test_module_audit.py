"""Tests for the audit module."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from github_app_geo_project.module.audit import _EventData, _process_renovate


@pytest.mark.asyncio
async def test_process_renovate_default_branch_success():
    """Test successful Renovate update on default branch."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="main")

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
        with patch("github_app_geo_project.module.audit.editor.EditRenovateConfigV2") as mock_editor:
            mock_config = MagicMock()
            mock_editor.return_value = MagicMock(__enter__=Mock(return_value=mock_config), __exit__=Mock())

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
                assert call_args[0][1] == "main"
                # Don't check exact path since function creates its own temp dir

                # Verify the renovate config was updated
                mock_config.__setitem__.assert_called_once_with("baseBranchPatterns", ["main", "1.0", "2.0"])

                mock_create_pr.assert_called_once()

                # Verify the call arguments
                pr_call_args = mock_create_pr.call_args
                assert pr_call_args[0][0] == "main"  # branch
                assert pr_call_args[0][1] == "ghci/audit/renovate/main"  # new_branch
                assert pr_call_args[0][2] == "Update Renovate configuration"  # key


@pytest.mark.asyncio
async def test_process_renovate_default_branch_clone_failure():
    """Test failed clone on default branch scenario."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="main")

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
