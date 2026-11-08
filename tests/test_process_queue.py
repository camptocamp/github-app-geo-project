# Copyright (c) 2026, Camptocamp SA

"""Tests for the process-queue script."""

import asyncio
import datetime as dt
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import githubkit.exception
import githubkit.response
import httpx
import pytest

from github_app_geo_project import models
from github_app_geo_project.module import ProcessOutput, modules
from github_app_geo_project.scripts.process_queue import (
    _Formatter,
    _Handler,
    _process_job,
    _process_one_job,
    _requeue_cancelled_job,
)


def test_requeue_cancelled_job() -> None:
    """Test that an interrupted job is put back to new with a log message."""
    job = MagicMock(id=42)
    root_logger = logging.getLogger()
    handler = _Handler(42, [], "INFO")
    handler.setFormatter(_Formatter("%(message)s"))

    _requeue_cancelled_job(job, root_logger, handler)

    assert job.status_enum == models.JobStatus.NEW
    assert len(handler.results) == 1
    record, _ = handler.results[0]
    assert "interrupted by shutdown" in record.getMessage()
    assert handler not in root_logger.handlers


@pytest.mark.asyncio
async def test_process_one_job_requeue_on_cancelled_error() -> None:
    """Test that a job interrupted by shutdown is requeued to new."""
    job = MagicMock(
        id=42,
        module="audit",
        module_event_name="cron",
        module_event_data={},
        github_event_data={},
        owner="camptocamp",
        repository="repo",
        priority=0,
        application="app",
    )
    session = MagicMock()
    session.bind = None
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.scalar = AsyncMock(return_value=0)
    session.run_sync = AsyncMock(return_value=False)

    with (
        patch(
            "github_app_geo_project.scripts.process_queue._validate_job",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "github_app_geo_project.scripts.process_queue._process_job",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch(
            "github_app_geo_project.scripts.process_queue._flush_job_logs",
            new=AsyncMock(),
        ) as flush_job_logs,
        pytest.raises(asyncio.CancelledError),
    ):
        await _process_one_job(job, session, make_pending=False, max_priority=0)

    assert job.status_enum == models.JobStatus.NEW
    flush_job_logs.assert_awaited_once()
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_process_job_skip_on_deleted_check_run() -> None:
    """Test that a job whose check run does not exist anymore is skipped."""
    job = MagicMock(
        id=42,
        module="dispatcher",
        module_event_name="workflow_job",
        module_event_data={},
        github_event_name="workflow_job",
        github_event_data={},
        owner="camptocamp",
        repository="repo",
        priority=0,
        application="main",
        check_run_id=107321157508,
        status_enum=None,
        finished_at=None,
    )
    session = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    rate_limit = MagicMock()
    rate_limit.resources.core.remaining = 5000
    rate_limit.resources.core.limit = 5000
    github_project = MagicMock()
    github_project.aio_github.rest.rate_limit.async_get = AsyncMock(
        return_value=MagicMock(parsed_data=rate_limit)
    )
    response = githubkit.response.Response(
        httpx.Response(404, request=httpx.Request("GET", "https://api.github.com/")),
        dict,
    )
    github_project.aio_github.rest.checks.async_get = AsyncMock(
        side_effect=githubkit.exception.RequestFailed(response)
    )

    root_logger = logging.getLogger()
    handler = _Handler(42, [], "INFO")
    handler.setFormatter(_Formatter("%(message)s"))

    with (
        patch("github_app_geo_project.scripts.process_queue.settings") as settings,
        patch("github_app_geo_project.scripts.process_queue.configuration") as configuration,
    ):
        settings.test.app_name = ""
        settings.service_url = "https://example.com/"
        configuration.get_github_application = AsyncMock(return_value=MagicMock())
        configuration.get_github_project = AsyncMock(return_value=github_project)
        configuration.get_configuration = AsyncMock(return_value={})

        result = await _process_job(session, root_logger, handler, job)

    assert result is True
    assert job.status_enum == models.JobStatus.SKIPPED
    assert isinstance(job.finished_at, dt.datetime)
    assert handler not in root_logger.handlers


async def _run_dashboard_job(
    monkeypatch: pytest.MonkeyPatch,
    existing_issues: list[MagicMock],
) -> MagicMock:
    """Run a job of a module that produces dashboard data and return the mocked GitHub project."""
    monkeypatch.delenv("GHCI_CREATE_DASHBOARD_ISSUE", raising=False)

    job = MagicMock(
        id=42,
        module="mockdash",
        module_event_name="cron",
        module_event_data={},
        github_event_name="cron",
        github_event_data={},
        owner="camptocamp",
        repository="repo",
        priority=0,
        application="main",
        check_run_id=None,
        status_enum=None,
        finished_at=None,
    )
    session = MagicMock()
    session.bind = None
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    rate_limit = MagicMock()
    rate_limit.resources.core.remaining = 5000
    rate_limit.resources.core.limit = 5000

    github_application = MagicMock()
    # The configuration key name, not used anymore for the dashboard issue title
    github_application.name = "main"
    github_application.slug = "geo-ghci-int"
    # The GitHub application display name, used for the dashboard issue title
    github_application.aio_application.name = "Geo GHCI int"

    github_project = MagicMock()
    github_project.application = github_application
    github_project.owner = "camptocamp"
    github_project.repository = "repo"
    github_project.aio_github.rest.rate_limit.async_get = AsyncMock(
        return_value=MagicMock(parsed_data=rate_limit)
    )
    github_project.aio_github.rest.issues.async_list_for_repo = AsyncMock(
        return_value=MagicMock(parsed_data=existing_issues)
    )
    github_project.aio_github.rest.issues.async_update = AsyncMock()
    github_project.aio_github.rest.issues.async_create = AsyncMock()
    github_project.aio_github.rest.checks.async_update = AsyncMock()

    mock_module = MagicMock()
    mock_module.title.return_value = "Mock module"
    mock_module.required_issue_dashboard.return_value = True
    mock_module.check_name_per_action.return_value = False
    mock_module.configuration_from_json.return_value = {}
    mock_module.event_data_from_json.return_value = {}
    mock_module.process = AsyncMock(
        return_value=ProcessOutput(dashboard="- [ ] Something to do <!-- mockdash -->")
    )

    root_logger = logging.getLogger()
    handler = _Handler(42, [], "INFO")
    handler.setFormatter(_Formatter("%(message)s"))

    with (
        patch("github_app_geo_project.scripts.process_queue.settings") as settings,
        patch("github_app_geo_project.scripts.process_queue.configuration") as configuration,
        patch(
            "github_app_geo_project.scripts.process_queue.utils.create_checks",
            new=AsyncMock(return_value=MagicMock(id=1234)),
        ),
        patch(
            "github_app_geo_project.scripts.process_queue._stream_job_logs",
            new=AsyncMock(),
        ),
        patch(
            "github_app_geo_project.scripts.process_queue._flush_job_logs",
            new=AsyncMock(),
        ),
        patch.dict(modules.MODULES, {"mockdash": mock_module}),
    ):
        settings.test.app_name = ""
        settings.service_url = "https://example.com/"
        settings.process_queue.logs_stream_interval.total_seconds.return_value = 60.0
        settings.process_queue.job_timeout.total_seconds.return_value = 60.0
        configuration.get_github_application = AsyncMock(return_value=github_application)
        configuration.get_github_project = AsyncMock(return_value=github_project)
        configuration.get_configuration = AsyncMock(return_value={})

        result = await _process_job(session, root_logger, handler, job)

    assert result is True
    assert job.status_enum == models.JobStatus.DONE
    mock_module.process.assert_awaited_once()
    return github_project


@pytest.mark.asyncio
async def test_process_job_dashboard_issue_created_with_application_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that a new dashboard issue is titled with the GitHub application name."""
    github_project = await _run_dashboard_job(monkeypatch, existing_issues=[])

    github_project.aio_github.rest.issues.async_update.assert_not_awaited()
    github_project.aio_github.rest.issues.async_create.assert_awaited_once()
    create_kwargs = github_project.aio_github.rest.issues.async_create.await_args.kwargs
    assert create_kwargs["title"] == "Geo GHCI int Dashboard"
    assert create_kwargs["owner"] == "camptocamp"
    assert create_kwargs["repo"] == "repo"
    assert "Something to do" in create_kwargs["body"]


@pytest.mark.asyncio
async def test_process_job_dashboard_issue_renamed_with_application_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that an existing dashboard issue with a wrong title is renamed on update."""
    existing_issue = MagicMock(number=486, body="Old body")
    existing_issue.title = "main Dashboard"
    github_project = await _run_dashboard_job(monkeypatch, existing_issues=[existing_issue])

    github_project.aio_github.rest.issues.async_create.assert_not_awaited()
    github_project.aio_github.rest.issues.async_update.assert_awaited_once()
    update_kwargs = github_project.aio_github.rest.issues.async_update.await_args.kwargs
    assert update_kwargs["issue_number"] == 486
    assert update_kwargs["title"] == "Geo GHCI int Dashboard"
    assert "Something to do" in update_kwargs["body"]
