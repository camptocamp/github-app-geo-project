# Copyright (c) 2026, Camptocamp SA

"""Tests for the application utils."""

from unittest.mock import AsyncMock, MagicMock, patch

import githubkit.exception
import githubkit.response
import httpx
import pytest
from sqlalchemy.dialects import postgresql

from github_app_geo_project import models, module, utils


def test_get_dashboard_issue_module() -> None:
    text = "Some text\n<!-- START module1 -->\n## Title\n\nContent\n<!-- END module1 -->\nOther text"
    current_module = "module1"
    result = utils.get_dashboard_issue_module(text, current_module)
    assert result == "Content"


def test_update_dashboard_issue_module() -> None:
    text = "Some text\n<!-- START module1 -->\n## Title\n\nContent\n<!-- END module1 -->\nOther text"
    module_name = "module1"
    current_module = type("Module", (object,), {"title": lambda _: "New Title"})()
    data = "New Content"
    result = utils.update_dashboard_issue_module(text, module_name, current_module, data)
    expected = (
        "Some text\n<!-- START module1 -->\n## New Title\n\nNew Content\n<!-- END module1 -->\nOther text"
    )
    assert result == expected


def test_merge_css_blocks_no_duplicates() -> None:
    """merge_css_blocks should merge rules with the same selector."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { font-weight: bold; }"])
    assert "color: red" in result
    assert "font-weight: bold" in result
    assert result.count(".a") == 1


def test_merge_css_blocks_no_duplicates_value() -> None:
    """merge_css_blocks should deduplicate identical rules."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { color: red; }"])
    assert result.count("color: red") == 1
    assert result.count(".a") == 1


def test_merge_css_blocks_multiple_selectors() -> None:
    """merge_css_blocks should handle multiple different selectors."""
    result = utils.merge_css_blocks([".a { color: red; }", ".b { color: blue; }"])
    assert ".a" in result
    assert ".b" in result
    assert "color: red" in result
    assert "color: blue" in result


def test_merge_css_blocks_backgrounds_priority() -> None:
    """merge_css_blocks should keep the last value for same property."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { color: blue; }"])
    assert "color: red" not in result
    assert "color: blue" in result


def _make_queue_job() -> models.Queue:
    """Create a queue job used by the apply_jobs_unique_on tests."""
    job = models.Queue()
    job.application = "app"
    job.module = "audit"
    job.owner = "camptocamp"
    job.repository = "geo-project"
    job.priority = 30
    job.github_event_name = "event"
    job.github_event_data = {"type": "event", "name": "daily"}
    job.module_event_name = "snyk (1.21)"
    job.module_event_data = {"type": "snyk", "version": "1.21"}
    return job


def _unique_module() -> MagicMock:
    """Create a module with jobs_unique_on enabled."""
    current_module = MagicMock()
    current_module.jobs_unique_on.return_value = [
        module.Fields.OWNER,
        module.Fields.REPOSITORY,
        module.Fields.MODULE_EVENT_NAME,
    ]
    return current_module


@pytest.mark.asyncio
async def test_apply_jobs_unique_on_disabled() -> None:
    """The jobs unique on without any field should not touch the queue."""
    session = MagicMock()
    session.execute = AsyncMock()
    current_module = MagicMock()
    current_module.jobs_unique_on.return_value = None

    await utils.apply_jobs_unique_on(session, current_module, _make_queue_job())

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_jobs_unique_on_skip_conflicting_job() -> None:
    """The jobs unique on should skip the conflicting new jobs and complete their check runs."""
    result = MagicMock()
    result.all.return_value = [(1, 123), (2, None)]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    github_project = MagicMock()
    github_project.aio_github.rest.checks.async_update = AsyncMock()

    await utils.apply_jobs_unique_on(session, _unique_module(), _make_queue_job(), github_project)

    session.execute.assert_awaited_once()
    sql = str(
        session.execute.await_args.args[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        ),
    )
    assert "UPDATE ghci.queue SET status='SKIPPED'" in sql
    assert "ghci.queue.status = 'NEW'" in sql
    assert "ghci.queue.application = 'app'" in sql
    assert "ghci.queue.module = 'audit'" in sql
    assert "ghci.queue.owner = 'camptocamp'" in sql
    assert "ghci.queue.repository = 'geo-project'" in sql
    assert "ghci.queue.module_event_name = 'snyk (1.21)'" in sql
    github_project.aio_github.rest.checks.async_update.assert_awaited_once_with(
        owner="camptocamp",
        repo="geo-project",
        check_run_id=123,
        status="completed",
        conclusion="skipped",
    )


@pytest.mark.asyncio
async def test_apply_jobs_unique_on_no_conflict() -> None:
    """The jobs unique on without conflicting job should not touch the check runs."""
    result = MagicMock()
    result.all.return_value = []
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    github_project = MagicMock()
    github_project.aio_github.rest.checks.async_update = AsyncMock()

    await utils.apply_jobs_unique_on(session, _unique_module(), _make_queue_job(), github_project)

    session.execute.assert_awaited_once()
    github_project.aio_github.rest.checks.async_update.assert_not_awaited()


def _request_failed(status_code: int) -> githubkit.exception.RequestFailed:
    """Create a failed GitHub API response, like the one raised on a transient 404."""
    return githubkit.exception.RequestFailed(
        githubkit.response.Response(
            httpx.Response(status_code, request=httpx.Request("PATCH", "https://api.github.com/")),
            dict,
        )
    )


@pytest.mark.asyncio
async def test_complete_check_run_retries_on_404() -> None:
    """The completed update should be retried while the check run is not visible on the API replica."""
    aio_github = MagicMock()
    aio_github.rest.checks.async_update = AsyncMock(side_effect=[_request_failed(404), MagicMock()])

    with patch("github_app_geo_project.utils.asyncio.sleep", new=AsyncMock()) as sleep:
        result = await utils.complete_check_run(aio_github, "camptocamp", "geo-project", 123, "success")

    assert result is True
    assert aio_github.rest.checks.async_update.await_count == 2
    sleep.assert_awaited_once()
    kwargs = aio_github.rest.checks.async_update.await_args.kwargs
    assert kwargs["status"] == "completed"
    assert kwargs["conclusion"] == "success"
    assert "output" not in kwargs


@pytest.mark.asyncio
async def test_complete_check_run_does_not_retry_other_errors() -> None:
    """Only the transient 404 of the eventual consistency is retried."""
    aio_github = MagicMock()
    aio_github.rest.checks.async_update = AsyncMock(side_effect=_request_failed(403))

    result = await utils.complete_check_run(
        aio_github, "camptocamp", "geo-project", 123, "failure", {"title": "Job failed", "summary": "Oops"}
    )

    assert result is False
    assert aio_github.rest.checks.async_update.await_count == 1
    assert aio_github.rest.checks.async_update.await_args.kwargs["output"] == {
        "title": "Job failed",
        "summary": "Oops",
    }


def _make_check_name_module(check_name_per_action: bool) -> MagicMock:
    """Create a module mock for the check name tests."""
    current_module = MagicMock()
    current_module.title.return_value = "Audit (Snyk/dpkg/Renovate)"
    current_module.check_name_per_action.return_value = check_name_per_action
    return current_module


def test_check_name_default() -> None:
    """Without opt-in the check name keeps the historical format."""
    job = _make_queue_job()

    assert utils.check_name(_make_check_name_module(False), job) == "Audit (Snyk/dpkg/Renovate): event"
    assert utils.check_output_title(_make_check_name_module(False), job) == "Audit (Snyk/dpkg/Renovate)"


def test_check_name_per_action() -> None:
    """With opt-in the module event name distinguishes the checks of the parallel jobs."""
    job = _make_queue_job()
    job.github_event_name = "repo_event"
    job.module_event_name = "dpkg (4.0)"

    assert (
        utils.check_name(_make_check_name_module(True), job)
        == "Audit (Snyk/dpkg/Renovate) - repo_event - dpkg (4.0)"
    )
    assert (
        utils.check_output_title(_make_check_name_module(True), job)
        == "Audit (Snyk/dpkg/Renovate) - dpkg (4.0)"
    )


def test_check_name_per_action_same_event_name() -> None:
    """With opt-in but no distinct module event name, the event name is not duplicated."""
    job = _make_queue_job()
    job.github_event_name = "dashboard"
    job.module_event_name = "dashboard"

    assert utils.check_name(_make_check_name_module(True), job) == "Audit (Snyk/dpkg/Renovate) - dashboard"
    assert utils.check_output_title(_make_check_name_module(True), job) == "Audit (Snyk/dpkg/Renovate)"
