# Copyright (c) 2026, Camptocamp SA

"""Tests for the process-queue script."""

import asyncio
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import githubkit.exception
import githubkit.response
import httpx
import pytest

from github_app_geo_project import models
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
    assert isinstance(job.finished_at, datetime.datetime)
    assert handler not in root_logger.handlers
