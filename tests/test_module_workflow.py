from unittest.mock import MagicMock

import github
import pytest

from github_app_geo_project.module.workflow import Workflow


@pytest.mark.asyncio
async def test_process_success() -> None:
    # Create a mock context
    context = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repository"
    repo = MagicMock()
    context.github_project.github.get_repo.return_value = repo
    repo.get_contents.side_effect = github.GithubException(status=404)
    repo.default_branch = "master"
    context.event_data = {
        "workflow_run": {
            "head_branch": "master",
            "conclusion": "success",
        },
        "workflow": {
            "name": "workflow_name",
        },
    }

    # Create an instance of the Workflow class
    workflow = Workflow()

    # Call the process method
    transversal_status = await workflow.update_transversal_status(
        context,
        None,
        {
            "owner/repository": {
                "workflow_name": {
                    "date": None,
                    "jobs": [],
                    "url": None,
                },
            },
        },
    )

    assert transversal_status == {}


@pytest.mark.asyncio
async def test_process_failure() -> None:
    # Create a mock context
    context = MagicMock()
    context.github_project.owner = "owner"
    context.github_project.repository = "repository"
    repo = MagicMock()
    context.github_project.github.get_repo.return_value = repo
    repo.get_contents.side_effect = github.GithubException(status=404)
    repo.default_branch = "master"
    context.event_data = {
        "workflow_run": {
            "head_branch": "master",
            "conclusion": "failure",
        },
        "workflow": {
            "name": "workflow_name",
        },
    }

    # Create an instance of the Workflow class
    workflow = Workflow()

    # Call the process method
    transversal_status = await workflow.update_transversal_status(context, None, {})

    assert "updated" in transversal_status["owner/repository"]
    del transversal_status["owner/repository"]["updated"]
    # Assert the expected output
    assert transversal_status == {
        "owner/repository": {
            "master": {
                "workflow_name": {
                    "date": None,
                    "jobs": [],
                    "url": None,
                },
            },
        },
    }
