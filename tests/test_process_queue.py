# Copyright (c) 2026, Camptocamp SA

"""Tests for the process-queue script."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from github_app_geo_project import models
from github_app_geo_project.scripts.process_queue import (
    _Formatter,
    _Handler,
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
