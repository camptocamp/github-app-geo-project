"""Tests for the audit module."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from github_app_geo_project.module.audit import _EventData, _process_renovate, _run_command_with_timeout


@pytest.mark.asyncio
async def test_process_renovate_default_branch_success():
    """Test successful Renovate update on default branch."""
    # Setup context
    context = Mock()
    context.module_event_data = _EventData(version=None)
    context.github_project = Mock()
    # Mock default_branch
    context.github_project.default_branch = AsyncMock(return_value="master")

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


@pytest.mark.asyncio
async def test_run_command_with_timeout_success():
    """Test successful command execution."""
    # Use a simple command that will complete quickly
    command = ["echo", "test output"]
    stdout, stderr, returncode = await _run_command_with_timeout(
        command, timeout=5, description="test echo"
    )
    
    assert stdout == b"test output\n"
    assert stderr == b""
    assert returncode == 0


@pytest.mark.asyncio
async def test_run_command_with_timeout_command_timeout():
    """Test command timeout with logging."""
    # Use a command that will timeout (sleep for longer than timeout)
    command = ["sleep", "10"]
    
    with pytest.raises(TimeoutError):
        await _run_command_with_timeout(command, timeout=1, description="test sleep")


@pytest.mark.asyncio
async def test_run_command_with_timeout_logs_output_on_timeout():
    """Test that stdout/stderr are logged on timeout."""
    # Create a command that produces output and then sleeps
    # Using sh -c to run a compound command
    command = ["sh", "-c", "echo 'output before timeout' && sleep 10"]
    
    with (
        pytest.raises(TimeoutError),
        patch("github_app_geo_project.module.audit._LOGGER") as mock_logger,
    ):
        await _run_command_with_timeout(command, timeout=1, description="test command")
    
    # Verify that warning was called with timeout information
    assert mock_logger.warning.called
    call_args = mock_logger.warning.call_args
    assert call_args[0][0] == "%s timed out after %d seconds\nstdout: %s\nstderr: %s"
    assert call_args[0][1] == "test command"
    assert call_args[0][2] == 1


@pytest.mark.asyncio
async def test_run_command_with_timeout_process_already_terminated():
    """Test timeout when process is already terminated."""
    # Mock a subprocess that terminates before we can kill it
    with patch("asyncio.create_subprocess_exec") as mock_create:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError())
        mock_proc.kill = Mock(side_effect=ProcessLookupError())
        mock_proc.stdout = None
        mock_create.return_value = mock_proc
        
        with pytest.raises(TimeoutError):
            await _run_command_with_timeout(
                ["test"], timeout=1, description="test command"
            )
        
        # Verify kill was attempted despite ProcessLookupError
        mock_proc.kill.assert_called_once()


@pytest.mark.asyncio
async def test_run_command_with_timeout_empty_output_buffers():
    """Test timeout with empty output buffers."""
    # Use a command that will timeout (sleep with no output)
    command = ["sleep", "10"]
    
    with (
        pytest.raises(TimeoutError),
        patch("github_app_geo_project.module.audit._LOGGER") as mock_logger,
    ):
        await _run_command_with_timeout(
            command, timeout=1, description="test command"
        )
    
    # Verify empty strings are logged when no output is available
    assert mock_logger.warning.called
    call_args = mock_logger.warning.call_args
    # stdout and stderr should be empty
    assert call_args[0][3] == ""  # stdout
    assert call_args[0][4] == ""  # stderr


@pytest.mark.asyncio
async def test_run_command_with_timeout_custom_working_directory():
    """Test command execution with custom working directory."""
    import tempfile
    from pathlib import Path
    import anyio
    
    # Create a temporary directory with a test file
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")
        
        # List files in the directory
        command = ["ls", "-1"]
        cwd = anyio.Path(tmpdir)
        stdout, stderr, returncode = await _run_command_with_timeout(
            command, cwd=cwd, timeout=5, description="test ls"
        )
        
        assert returncode == 0
        assert b"test.txt" in stdout
        assert stderr == b""


@pytest.mark.asyncio
async def test_run_command_with_timeout_nonzero_exit_code():
    """Test command that exits with non-zero code."""
    # Use a command that will fail
    command = ["ls", "/nonexistent_directory_12345"]
    stdout, stderr, returncode = await _run_command_with_timeout(
        command, timeout=5, description="test failing ls"
    )
    
    # Command should complete but with non-zero exit code
    assert returncode != 0
    assert b"No such file or directory" in stderr or b"cannot access" in stderr
