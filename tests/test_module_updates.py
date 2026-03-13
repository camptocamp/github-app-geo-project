import base64
from unittest.mock import AsyncMock, MagicMock, patch

import githubkit.versions.latest.models
import pytest

from github_app_geo_project import module
from github_app_geo_project.module import updates
from github_app_geo_project.module.updates import configuration


@pytest.fixture
def mock_github_project():
    project = MagicMock()
    project.default_branch = AsyncMock(return_value="main")
    project.aio_github.rest.repos.async_get_content = AsyncMock()
    return project


@pytest.fixture
def mock_context(mock_github_project):
    context = MagicMock(spec=module.ProcessContext)
    context.github_project = mock_github_project
    context.module_config = configuration.UpdatesConfiguration(enabled=True)
    context.module_event_data = updates.UpdatesEventData()
    return context


@pytest.mark.asyncio
async def test_get_actions():
    updates_module = updates.Updates()
    context = module.GetActionContext(
        github_event_name="repository_dispatch",
        github_event_data={"type": "event", "name": "updates-cron"},
        module_event_name="updates",
        owner="camptocamp",
        repository="test",
        github_application=MagicMock(),
    )
    actions = updates_module.get_actions(context)
    assert len(actions) == 1
    assert actions[0].priority == module.PRIORITY_CRON
    assert actions[0].data.step == updates.Step.INITIAL


@pytest.mark.asyncio
async def test_process_discovery(mock_context):
    updates_module = updates.Updates()
    mock_context.module_event_data.step = updates.Step.INITIAL

    # Mock SECURITY.md content
    content = "| Version | Supported Until |\n|---|---|\n| 1.0 | 01/01/2025 |"

    # Mocking the response object structure for async_get_content
    mock_response = MagicMock()
    mock_response.parsed_data = MagicMock(spec=githubkit.versions.latest.models.ContentFile)
    mock_response.parsed_data.content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    mock_context.github_project.aio_github.rest.repos.async_get_content.return_value = mock_response

    output = await updates_module.process(mock_context)

    assert len(output.actions) == 2  # main + 1.0
    assert output.actions[0].data.branch == "1.0"
    assert output.actions[0].data.step == updates.Step.BRANCH
    assert output.actions[1].data.branch == "main"
    assert output.actions[1].data.step == updates.Step.BRANCH
    assert output.actions[0].priority == module.PRIORITY_CRON


@pytest.mark.asyncio
async def test_process_worker(mock_context):
    updates_module = updates.Updates()
    mock_context.module_event_data.step = updates.Step.BRANCH
    mock_context.module_event_data.branch = "main"

    with patch.object(updates_module, "_process_branch", new_callable=AsyncMock) as mock_process_branch:
        await updates_module.process(mock_context)

        mock_process_branch.assert_called_with(mock_context, "main")


@patch("github_app_geo_project.module.updates.module_utils")
@patch("github_app_geo_project.module.updates.mra.EditYAML")
@pytest.mark.asyncio
async def test_process_branch(mock_edit_yaml, mock_utils, mock_context, tmp_path):
    mock_utils.git_clone = AsyncMock(return_value=tmp_path)
    mock_utils.create_commit_pull_request = AsyncMock()

    updates_module = updates.Updates()

    # Setup tmp_path with .pre-commit-config.yaml
    config_file = tmp_path / ".pre-commit-config.yaml"
    # Create the file so .exists() returns True
    config_file.touch()

    # Prepare the config data that EditYAML will yield
    config_data = {
        "repos": [
            {
                "repo": "https://github.com/mheap/json-schema-spell-checker",
                "rev": "main",
                "hooks": [{"id": "json-schema-spell-checker"}],
            }
        ]
    }
    mock_edit_yaml.return_value.__enter__.return_value = config_data

    # Mock TemporaryDirectory to return tmp_path
    with patch("tempfile.TemporaryDirectory") as mock_temp_dir:
        mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)

        # Mock versions.yaml reading inside _process_branch
        versions = {"mheap/json-schema-spell-checker": "0.1.0"}
        with (
            patch("yaml.safe_load", return_value=versions),
            patch("pathlib.Path.open"),  # Mocking file opening since we mock yaml.safe_load
        ):
            await updates_module._process_branch(mock_context, "main")

        # Verify git_clone was called
        mock_utils.git_clone.assert_called_once()

        # Verify config data was updated
        assert config_data["repos"][0]["rev"] == "0.1.0"

        # Verify create_commit_pull_request was called
        mock_utils.create_commit_pull_request.assert_called_once()


@patch("github_app_geo_project.module.updates.module_utils")
@patch("github_app_geo_project.module.updates.mra.EditYAML")
@pytest.mark.asyncio
async def test_process_branch_no_update(mock_edit_yaml, mock_utils, mock_context, tmp_path):
    mock_utils.git_clone = AsyncMock(return_value=tmp_path)
    mock_utils.create_commit_pull_request = AsyncMock()

    updates_module = updates.Updates()

    # Setup tmp_path with .pre-commit-config.yaml
    config_file = tmp_path / ".pre-commit-config.yaml"
    # Create the file so .exists() returns True
    config_file.touch()

    # Prepare the config data that EditYAML will yield
    config_data = {
        "repos": [
            {
                "repo": "https://github.com/mheap/json-schema-spell-checker",
                "rev": "0.0.1",
                "hooks": [{"id": "json-schema-spell-checker"}],
            }
        ]
    }
    mock_edit_yaml.return_value.__enter__.return_value = config_data

    # Mock TemporaryDirectory to return tmp_path
    with patch("tempfile.TemporaryDirectory") as mock_temp_dir:
        mock_temp_dir.return_value.__enter__.return_value = str(tmp_path)

        # Mock versions.yaml reading inside _process_branch
        versions = {"mheap/json-schema-spell-checker": "0.1.0"}
        with patch("yaml.safe_load", return_value=versions), patch("pathlib.Path.open"):
            await updates_module._process_branch(mock_context, "main")

        # Verify git_clone was called
        mock_utils.git_clone.assert_called_once()

        # Verify config data was NOT updated
        assert config_data["repos"][0]["rev"] == "0.0.1"

        # Verify create_commit_pull_request was NOT called
        mock_utils.create_commit_pull_request.assert_not_called()
