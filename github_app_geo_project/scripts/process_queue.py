"""Process the jobs present in the database queue."""

import argparse
import asyncio
import concurrent
import contextlib
import contextvars
import datetime
import functools
import inspect
import io
import logging
import os
import signal
import socket
import subprocess  # nosec
import sys
import threading
import time
import urllib.parse
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import aiomonitor
import anyio
import c2casgiutils.config
import githubkit.exception
import githubkit.webhooks
import githubkit_schemas.latest.models
import prometheus_client.exposition
import sentry_sdk
import sqlalchemy.ext.asyncio
import sqlalchemy.orm
from prometheus_client import Gauge

from github_app_geo_project import (
    configuration,
    models,
    module,
    project_configuration,
    utils,
)
from github_app_geo_project.module import GHCIError, modules
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.settings import settings

if TYPE_CHECKING:
    import types

_LOGGER = logging.getLogger(__name__)
_LOGGER_WSGI = logging.getLogger("prometheus_client.wsgi")

_NB_JOBS = Gauge("ghci_jobs_number", "Number of jobs", ["status"])
_MODULE_STATUS_LOCK: dict[str, asyncio.Lock] = {}


class _JobInfo(NamedTuple):
    module: str
    module_event_name: str
    repository: str
    priority: int
    worker_max_priority: int


_RUNNING_JOBS: dict[int, _JobInfo] = {}

_LAST_RUN_TIME = time.time()
_FLUSH_LOCK = asyncio.Lock()


class _Handler(logging.Handler):
    context_var: contextvars.ContextVar[int] = contextvars.ContextVar("job_id")

    def __init__(self, job_id: int) -> None:
        super().__init__()
        self.results: list[tuple[logging.LogRecord, str | None]] = []
        self.last_written_index = 0
        self.job_id = job_id
        self.context_var.set(job_id)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.context_var.get() != self.job_id:
                return
        except LookupError:
            return
        css_style = None
        if isinstance(record.msg, module_utils.Message):
            css_style = record.msg.css_style
            record.msg = record.msg.to_html(style="collapse")
        self.results.append((record, css_style))


class _Formatter(logging.Formatter):
    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        str_msg = super().formatMessage(record).strip()
        level_class = record.levelname.lower()
        attributes = f' class="level-{level_class}"'
        result = f"<p{attributes}>{str_msg}</p>"

        str_msg = record.message.strip()
        if not str_msg.startswith("<p>") and not str_msg.endswith("<div>"):
            result += f"<p>{str_msg}</p>"
        else:
            result += str_msg

        return result

    def format(self, record: logging.LogRecord) -> str:
        str_msg = super().format(record).strip()
        return f"<pre>{str_msg}</pre>"


async def _flush_job_logs(
    session_factory: sqlalchemy.ext.asyncio.async_sessionmaker[sqlalchemy.ext.asyncio.AsyncSession],
    handler: _Handler,
    job_id: int,
) -> None:
    async with _FLUSH_LOCK:
        new_entries = handler.results[handler.last_written_index :]
        if not new_entries:
            return
        async with session_factory() as log_session:
            for entry, css_style in new_entries:
                log_session.add(
                    models.JobLogEntry(
                        job_id=job_id,
                        log=handler.format(entry),
                        css_style=css_style,
                        level_name=entry.levelname,
                        level_no=entry.levelno,
                        filename=entry.pathname,
                    )
                )
            await log_session.commit()
        handler.last_written_index += len(new_entries)


async def _stream_job_logs(
    session_factory: sqlalchemy.ext.asyncio.async_sessionmaker[sqlalchemy.ext.asyncio.AsyncSession],
    handler: _Handler,
    job_id: int,
    interval: float,
) -> None:
    while True:
        await asyncio.sleep(interval)
        await _flush_job_logs(session_factory, handler, job_id)


async def _validate_job(
    application: str,
    event_data: dict[str, Any],
) -> bool:
    if settings.test.app_name:
        return True
    github_application = await configuration.get_github_application(application)
    installation_id = event_data.get("installation", {}).get("id", 0)
    if github_application.id == installation_id:
        _LOGGER.error(
            "Invalid installation id %i != %i",
            github_application.id,
            installation_id,
        )
        return False
    return True


async def _process_job(
    session: sqlalchemy.ext.asyncio.AsyncSession,
    root_logger: logging.Logger,
    handler: _Handler,
    job: models.Queue,
) -> bool:
    current_module = modules.MODULES.get(job.module)
    if current_module is None:
        _LOGGER.error("Unknown module %s", job.module)
        return False

    logs_url = settings.service_url
    logs_url = logs_url if logs_url.endswith("/") else logs_url + "/"
    logs_url = urllib.parse.urljoin(logs_url, "logs/")
    logs_url = urllib.parse.urljoin(logs_url, str(job.id))

    new_issue_data = None
    issue_data = ""
    module_config: project_configuration.ModuleConfiguration = {}
    github_project: configuration.GithubProject | None = None
    check_run: githubkit_schemas.latest.models.CheckRun | None = None
    tasks: list[asyncio.Task[Any]] = []
    if not settings.test.app_name:
        github_application = await configuration.get_github_application(
            job.application,
        )
        if job.owner is not None and job.repository is not None:
            github_project = await configuration.get_github_project(
                github_application,
                job.owner,
                job.repository,
            )
            # Get Rate limit status
            rate_limit = (await github_project.aio_github.rest.rate_limit.async_get()).parsed_data
            if rate_limit.resources.core.remaining < 1000:
                _LOGGER.warning(
                    "Rate limit status: %s/%s",
                    rate_limit.resources.core.remaining,
                    rate_limit.resources.core.limit,
                )
                # Wait until github_project.github.rate_limiting_resettime
                await asyncio.sleep(
                    max(
                        0,
                        rate_limit.resources.core.reset - time.time(),
                    ),
                )

            if current_module.required_issue_dashboard():
                dashboard_issue = await _get_dashboard_issue(github_project)
                if dashboard_issue:
                    issue_full_data = dashboard_issue.body
                    assert isinstance(issue_full_data, str)
                    issue_data = utils.get_dashboard_issue_module(
                        issue_full_data,
                        job.module,
                    )

            module_config = cast(
                "project_configuration.ModuleConfiguration",
                (await configuration.get_configuration(github_project)).get(
                    job.module,
                    {},
                ),
            )
            if job.check_run_id is not None:
                check_run = (
                    await github_project.aio_github.rest.checks.async_get(
                        owner=job.owner,
                        repo=job.repository,
                        check_run_id=job.check_run_id,
                    )
                ).parsed_data
        else:
            github_project = configuration.GithubProject(
                application=github_application,
                token=None,
                owner=None,
                repository=None,
                aio_installation=None,
                aio_github=None,
            )

    if module_config.get("enabled", project_configuration.MODULE_ENABLED_DEFAULT):
        try:
            if not settings.test.app_name:
                if job.check_run_id is None and job.owner is not None and job.repository is not None:
                    check_run = await module_utils.create_checks(
                        job,
                        session,
                        current_module,
                        github_project,
                        settings.service_url,
                    )

                if github_project is not None and github_project.aio_github is not None:
                    assert check_run is not None
                    await github_project.aio_github.rest.checks.async_update(
                        owner=job.owner,
                        repo=job.repository,
                        check_run_id=check_run.id,
                        external_id=str(job.id),
                        status="in_progress",
                        details_url=logs_url,
                    )

            # Close transaction if one is open
            await session.commit()
            await session.refresh(job)
            context = module.ProcessContext(
                session=session,
                github_project=github_project,  # type: ignore[arg-type]
                github_event_name=job.github_event_name,
                github_event_data=job.github_event_data,
                module_config=current_module.configuration_from_json(
                    cast("dict[str, Any]", module_config),
                ),
                module_event_name=job.module_event_name,
                module_event_data=current_module.event_data_from_json(
                    job.module_event_data,
                ),
                issue_data=issue_data,
                job_id=job.id,
                service_url=settings.service_url,
            )
            root_logger.addHandler(handler)
            old_level = root_logger.level
            root_logger.setLevel(logging.DEBUG)
            log_task = None
            log_interval = settings.process_queue.logs_stream_interval.total_seconds()
            log_session_factory = sqlalchemy.ext.asyncio.async_sessionmaker(
                bind=session.bind,
            )
            log_task = asyncio.create_task(
                _stream_job_logs(
                    log_session_factory,
                    handler,
                    job.id,
                    log_interval,
                ),
                name=f"Stream logs {job.id}",
            )
            result = None
            try:
                start = datetime.datetime.now(tz=datetime.UTC)
                job_timeout = settings.process_queue.job_timeout.total_seconds()
                transversal_status = None
                async with asyncio.timeout(job_timeout):
                    task = asyncio.create_task(
                        current_module.process(context),
                        name=f"Process Job {job.id} - {job.module_event_name} - {job.module or '-'}",
                    )
                    result = await task
                    if result.updated_transversal_status:
                        root_logger.removeHandler(handler)
                        await session.refresh(job)
                        if job.module not in _MODULE_STATUS_LOCK:
                            _MODULE_STATUS_LOCK[job.module] = asyncio.Lock()
                        async with _MODULE_STATUS_LOCK[job.module]:
                            module_status = (
                                (
                                    await session.execute(
                                        sqlalchemy.select(models.ModuleStatus)
                                        .where(models.ModuleStatus.module == job.module)
                                        .with_for_update(),
                                    )
                                )
                                .scalars()
                                .one_or_none()
                            )
                            root_logger.addHandler(handler)
                            transversal_status = current_module.transversal_status_from_json(
                                (module_status.data if module_status is not None else None),
                            )
                            transversal_status = await current_module.update_transversal_status(
                                context,
                                result.intermediate_status,
                                transversal_status,
                            )
                            if transversal_status is not None:
                                root_logger.removeHandler(handler)
                                root_logger.setLevel(old_level)
                                _LOGGER.debug(
                                    "Update module status %s `%s` (job id: %i, type: %s, %s)\n%s",
                                    job.module,
                                    current_module.title(),
                                    job.id,
                                    type(transversal_status),
                                    transversal_status,
                                    current_module.transversal_status_to_json(
                                        transversal_status,
                                    ),
                                )
                                if module_status is None:
                                    module_status = models.ModuleStatus(
                                        module=job.module,
                                        data=current_module.transversal_status_to_json(
                                            transversal_status,
                                        ),
                                    )
                                    session.add(module_status)
                                else:
                                    await session.execute(
                                        sqlalchemy.update(models.ModuleStatus)
                                        .where(models.ModuleStatus.module == job.module)
                                        .values(
                                            data=current_module.transversal_status_to_json(
                                                transversal_status,
                                            ),
                                        ),
                                    )
                                del module_status
                                root_logger.addHandler(handler)

                root_logger.removeHandler(handler)
                await session.refresh(job)
                root_logger.addHandler(handler)
                _LOGGER.debug(
                    "Module %s took %s",
                    job.module,
                    datetime.datetime.now(tz=datetime.UTC) - start,
                )

                if result is not None:
                    non_none = [
                        *(["dashboard"] if result.dashboard is not None else []),
                        *(["transversal_status"] if transversal_status is not None else []),
                        *([f"{len(result.actions)} action(s)"] if result.actions else []),
                        *(["output"] if result.output is not None else []),
                    ]
                    if non_none:
                        _LOGGER.info(
                            "Module %s finished with: %s",
                            job.module,
                            ", ".join(non_none),
                        )

                        if result.actions:
                            _LOGGER.debug(
                                "Actions: %s",
                                ", ".join(
                                    [a.title or "Untitled" for a in result.actions],
                                ),
                            )
                    else:
                        _LOGGER.info("Module %s finished", job.module)

                    if not result.success:
                        _LOGGER.warning("Module %s failed", job.module)
                else:
                    _LOGGER.info("Module %s finished with None result", job.module)
            except GHCIError:
                raise
            except Exception as exception:  # pylint: disable=broad-exception-caught
                root_logger.removeHandler(handler)
                await session.refresh(job)
                root_logger.addHandler(handler)
                # For the logs view
                _LOGGER.exception(
                    "Failed to process job id: %s on module: %s",
                    job.id,
                    job.module,
                )
                error_message = f"Failed to process job id: {job.id} on module: {job.module}"
                raise GHCIError(error_message) from exception
            finally:
                root_logger.setLevel(old_level)
                root_logger.removeHandler(handler)
                if log_task is not None:
                    log_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await log_task
                await _flush_job_logs(log_session_factory, handler, job.id)

            if github_project is not None and github_project.aio_github is not None:
                check_output = {
                    "summary": (
                        "Module executed successfully"
                        if result is None or result.success
                        else "Module failed"
                    ),
                }
                if result is not None and not result.success:
                    check_output["text"] = f"[See logs for more details]({logs_url})"
                if result is not None and result.output:
                    check_output.update(result.output)
                assert check_run is not None
                if len(check_output.get("summary", "")) > 65535:
                    check_output["summary"] = check_output["summary"][:65532] + "..."
                if len(check_output.get("text", "")) > 65535:
                    check_output["text"] = check_output["text"][:65532] + "..."
                try:
                    _LOGGER.debug("Update check run %s", job.check_run_id)
                    tasks.append(
                        asyncio.create_task(
                            github_project.aio_github.rest.checks.async_update(
                                owner=job.owner,
                                repo=job.repository,
                                check_run_id=check_run.id,
                                status="completed",
                                conclusion=("success" if result is None or result.success else "failure"),
                                output={
                                    "title": check_output.get(
                                        "title",
                                        current_module.title(),
                                    ),
                                    "summary": check_output["summary"],
                                    "text": check_output.get("text", ""),
                                },
                            ),
                            name=f"Update check run {job.check_run_id}",
                        ),
                    )
                except githubkit.exception.RequestFailed as exception:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        exception.response.text,
                        (
                            "\n".join(f"{k}: {v}" for k, v in exception.response.headers.items())
                            if exception.response.headers
                            else ""
                        ),
                        exception.response.reason_phrase,
                        exception.response.status_code,
                    )
                except TimeoutError:
                    _LOGGER.exception(
                        "Timeout while updating check run %s",
                        job.check_run_id,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    _LOGGER.exception(
                        "Failed to update check run %s",
                        job.check_run_id,
                    )

            job.status_enum = (
                models.JobStatus.DONE if result is None or result.success else models.JobStatus.ERROR
            )
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)

            job.log = None
            if result is not None and github_project is not None and github_project.aio_github is not None:
                _LOGGER.debug("Process actions")
                # Store needed values locally to avoid accessing the job object during transaction
                job_priority = job.priority
                job_application = job.application
                job_owner = job.owner
                job_repository = job.repository
                job_github_event_name = job.github_event_name
                job_github_event_data = job.github_event_data
                job_module = job.module
                job_module_event_name = job.module_event_name

                for action in result.actions:
                    new_job = models.Queue()
                    new_job.priority = action.priority if action.priority >= 0 else job_priority
                    new_job.application = job_application
                    new_job.owner = job_owner
                    new_job.repository = job_repository
                    new_job.github_event_name = job_github_event_name
                    new_job.github_event_data = job_github_event_data
                    new_job.module = job_module
                    new_job.module_event_name = action.title or job_module_event_name
                    new_job.module_event_data = current_module.event_data_to_json(
                        action.data,
                    )
                    session.add(new_job)
                    await module_utils.create_checks(
                        new_job,
                        session,
                        current_module,
                        github_project,
                        settings.service_url,
                    )

            new_issue_data = result.dashboard if result is not None else None
            _LOGGER.debug("Job queue updated")
        except githubkit.exception.RequestFailed as exception:
            job.status_enum = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            root_logger.addHandler(handler)
            try:
                _LOGGER.exception(
                    "Failed to process job id: %s on module: %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                    job.id,
                    job.module,
                    exception.response.text,
                    (
                        "\n".join(f"{k}: {v}" for k, v in exception.response.headers.items())
                        if exception.response.headers
                        else ""
                    ),
                    exception.response.reason_phrase,
                    exception.response.status_code,
                )
            finally:
                root_logger.removeHandler(handler)
            if check_run is None:
                raise
            try:
                if github_project is not None and github_project.aio_github is not None:
                    await github_project.aio_github.rest.checks.async_update(
                        owner=job.owner,
                        repo=job.repository,
                        check_run_id=check_run.id,
                        status="completed",
                        conclusion="failure",
                        output={
                            "summary": f"Unexpected error: {exception}\n[See logs for more details]({logs_url})",
                        },
                    )
            except githubkit.exception.RequestFailed as github_exception:
                root_logger.addHandler(handler)
                try:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        github_exception.response.text,
                        (
                            "\n".join(f"{k}: {v}" for k, v in github_exception.response.headers.items())
                            if github_exception.response.headers
                            else ""
                        ),
                        github_exception.response.reason_phrase,
                        github_exception.response.status_code,
                    )
                finally:
                    root_logger.removeHandler(handler)
            raise
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as proc_error:
            job.status_enum = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)

            message = module_utils.AnsiProcessMessage(
                cast("list[str]", proc_error.cmd),
                (None if isinstance(proc_error, subprocess.TimeoutExpired) else proc_error.returncode),
                proc_error.output,
                cast("str", proc_error.stderr),
            )
            message.title = f"Error process job '{job.id}' on module: {job.module}"
            root_logger.addHandler(handler)
            try:
                _LOGGER.exception(message)
            finally:
                root_logger.removeHandler(handler)
            if check_run is None:
                raise
            try:
                if github_project is not None and github_project.aio_github is not None:
                    await github_project.aio_github.rest.checks.async_update(
                        owner=job.owner,
                        repo=job.repository,
                        check_run_id=check_run.id,
                        status="completed",
                        conclusion="failure",
                        output={
                            "summary": f"Unexpected error: {proc_error}\n[See logs for more details]({logs_url})",
                        },
                    )
            except githubkit.exception.RequestFailed as exception:
                _LOGGER.exception(
                    "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                    job.check_run_id,
                    exception.response.text,
                    (
                        "\n".join(f"{k}: {v}" for k, v in exception.response.headers.items())
                        if exception.response.headers
                        else ""
                    ),
                    exception.response.reason_phrase,
                    exception.response.status_code,
                )
            raise
        except Exception as exception:
            job.status_enum = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            if not isinstance(exception, GHCIError):
                root_logger.addHandler(handler)
                try:
                    _LOGGER.exception(
                        "Failed to process job id: %s on module: %s",
                        job.id,
                        job.module,
                    )
                finally:
                    root_logger.removeHandler(handler)
            if check_run is not None and github_project is not None and github_project.aio_github is not None:
                try:
                    await github_project.aio_github.rest.checks.async_update(
                        owner=job.owner,
                        repo=job.repository,
                        check_run_id=check_run.id,
                        status="completed",
                        conclusion="failure",
                        output={
                            "summary": f"Unexpected error: {exception}\n[See logs for more details]({logs_url}))",
                        },
                    )
                except githubkit.exception.RequestFailed as github_exception:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        github_exception.response.text,
                        (
                            "\n".join(f"{k}: {v}" for k, v in github_exception.response.headers.items())
                            if github_exception.response.headers
                            else ""
                        ),
                        github_exception.response.reason_phrase,
                        github_exception.response.status_code,
                    )
            raise
    else:
        try:
            _LOGGER.info("Module %s is disabled", job.module)
            job.status_enum = models.JobStatus.SKIPPED
            if check_run is not None and github_project is not None and github_project.aio_github is not None:
                await github_project.aio_github.rest.checks.async_update(
                    owner=job.owner,
                    repo=job.repository,
                    check_run_id=check_run.id,
                    status="completed",
                    conclusion="skipped",
                )

            current_module.cleanup(
                module.CleanupContext(
                    github_project=github_project,  # type: ignore[arg-type]
                    github_event_name="event",
                    github_event_data=job.github_event_data,
                    module_event_name="event",
                    module_event_data=job.module_event_data,
                ),
            )
        except Exception:
            _LOGGER.exception(
                "Failed to cleanup job id: %s on module: %s, module data:\n%s\nevent data:\n%s",
                job.id,
                job.module,
                job.module_event_data,
                job.github_event_data,
            )
            raise

    if (
        github_project is not None
        and github_project.aio_github is not None
        and current_module.required_issue_dashboard()
        and new_issue_data is not None
    ):
        _LOGGER.debug("Update dashboard issue")
        dashboard_issue = await _get_dashboard_issue(github_project)

        if dashboard_issue:
            body = dashboard_issue.body
            assert isinstance(body, str)
            issue_full_data = utils.update_dashboard_issue_module(
                body,
                job.module,
                current_module,
                new_issue_data,
            )
            _LOGGER.debug(
                "Update issue %s, with:\n%s",
                dashboard_issue.number,
                issue_full_data,
            )
            if github_project is not None:
                try:
                    await github_project.aio_github.rest.issues.async_update(
                        owner=job.owner,
                        repo=job.repository,
                        issue_number=dashboard_issue.number,
                        body=issue_full_data,
                    )
                except githubkit.exception.RequestFailed as error:
                    _LOGGER.warning(
                        "Failed to update issue %s on repository %s/%s: %s",
                        dashboard_issue.number,
                        job.owner,
                        job.repository,
                        error,
                    )
        elif new_issue_data and os.environ.get(
            "GHCI_CREATE_DASHBOARD_ISSUE",
            "1",
        ).lower() in (
            "1",
            "true",
            "on",
        ):
            issue_full_data = utils.update_dashboard_issue_module(
                f"This issue is the dashboard used by GHCI modules.\n\n[Project on GHCI]({settings.service_url}project/{job.owner}/{job.repository})\n\n",
                job.module,
                current_module,
                new_issue_data,
            )
            if github_project is not None:
                await github_project.aio_github.rest.issues.async_create(
                    owner=job.owner,
                    repo=job.repository,
                    title=f"{github_application.name} Dashboard",
                    body=issue_full_data,
                )

    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)

    return True


async def _get_dashboard_issue(
    github_project: configuration.GithubProject,
) -> githubkit_schemas.latest.models.Issue | None:
    open_issues = (
        await github_project.aio_github.rest.issues.async_list_for_repo(
            owner=github_project.owner,
            repo=github_project.repository,
            state="open",
            creator=f"{github_project.application.slug}[bot]",
        )
    ).parsed_data
    # TODO: delete duplicated issues # noqa: TD003
    if isinstance(open_issues, list):
        for candidate in open_issues:  # type: ignore[attr-defined]
            if "dashboard" in candidate.title.lower().split():
                return candidate  # type: ignore[no-any-return]
    return None


async def _process_dashboard_issue(
    session: sqlalchemy.ext.asyncio.AsyncSession,
    event_data: dict[str, Any],
    application: str,
    owner: str,
    repository: str,
) -> None:
    """Process changes on the dashboard issue."""
    github_application = await configuration.get_github_application(application)
    github_project = await configuration.get_github_project(
        github_application,
        owner,
        repository,
    )
    event_data_issue = githubkit.webhooks.parse_obj("issues", event_data)

    if not isinstance(event_data_issue, githubkit_schemas.latest.models.WebhookIssuesEdited):
        _LOGGER.debug("Dashboard issue not edited")
        return

    if event_data_issue.issue.user is None:
        _LOGGER.warning("No user in the event data")
        return

    if event_data_issue.issue.user.login == f"{github_application.slug}[bot]":
        dashboard_issue = await _get_dashboard_issue(github_project)

        if dashboard_issue and dashboard_issue.number == event_data_issue.issue.number:
            _LOGGER.debug("Dashboard issue edited")
            old_data = (
                event_data_issue.changes.body.from_
                if event_data_issue.changes and event_data_issue.changes.body
                else ""
            )
            new_data = event_data_issue.issue.body or ""

            app_config = settings.application_configs.get(github_project.application.name)
            for name in app_config.modules if app_config is not None else []:
                current_module = modules.MODULES.get(name)
                if current_module is None:
                    _LOGGER.error("Unknown module %s", name)
                    continue
                module_old = utils.get_dashboard_issue_module(old_data, name)
                module_new = utils.get_dashboard_issue_module(new_data, name)
                if module_old != module_new:
                    _LOGGER.debug(
                        "Dashboard issue edited for module %s: %s",
                        name,
                        current_module.title(),
                    )
                    if current_module.required_issue_dashboard():
                        for action in current_module.get_actions(
                            module.GetActionContext(
                                github_event_name="dashboard",
                                github_event_data={
                                    "type": "dashboard",
                                    "old_data": module_old,
                                    "new_data": module_new,
                                },
                                owner=github_project.owner,
                                repository=github_project.repository,
                                github_application=github_project.application,
                                module_event_name="dashboard",
                            ),
                        ):
                            job = models.Queue()
                            job.priority = (
                                action.priority if action.priority >= 0 else module.PRIORITY_DASHBOARD
                            )
                            job.application = github_project.application.name
                            job.owner = github_project.owner
                            job.repository = github_project.repository
                            job.github_event_name = "dashboard"
                            job.github_event_data = {
                                "type": "dashboard",
                                "old_data": module_old,
                                "new_data": module_new,
                            }
                            job.module = name
                            job.module_event_name = action.title or "dashboard"
                            job.module_event_data = current_module.event_data_to_json(
                                action.data,
                            )
                            session.add(job)
                            await session.flush()
                            if action.checks:
                                await module_utils.create_checks(
                                    job,
                                    session,
                                    current_module,
                                    github_project,
                                    settings.service_url,
                                )
                            await session.commit()
                            await session.refresh(job)
    else:
        _LOGGER.debug(
            "Dashboard event ignored %s!=%s",
            event_data_issue.issue.user.login,
            f"{github_application.slug}[bot]",
        )


# Where 2147483647 is the PostgreSQL max int, see: https://www.postgresql.org/docs/current/datatype-numeric.html
async def _get_process_one_job(
    Session: sqlalchemy.ext.asyncio.async_sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
        sqlalchemy.ext.asyncio.AsyncSession
    ],
    no_steal_long_pending: bool = False,
    make_pending: bool = False,
    max_priority: int = 2147483647,
) -> bool:
    _LOGGER.debug("Process one job (max priority: %i): Start", max_priority)
    async with Session() as session:
        job = (
            await session.execute(
                sqlalchemy.select(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.NEW.name,
                    models.Queue.priority <= max_priority,
                )
                .order_by(
                    models.Queue.priority.asc(),
                    models.Queue.created_at.asc(),
                )
                .with_for_update(skip_locked=True),
            )
        ).scalar()

        if job is None:
            if no_steal_long_pending:
                _LOGGER.debug(
                    "Process one job (max priority: %i): No job to process",
                    max_priority,
                )
                return True

            # Very long pending job => error
            # Calculate the timestamp threshold for marking jobs as error
            error_threshold = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(
                seconds=settings.process_queue.job_timeout_error.total_seconds(),
            )
            result = await session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING.name,
                    models.Queue.created_at < error_threshold,
                )
                .values(status=models.JobStatus.ERROR.name),
            )
            affected_rows = result.rowcount  # type: ignore[attr-defined]
            if affected_rows:
                _LOGGER.error(
                    "Error: %i long started jobs marked as error",
                    affected_rows,
                )

            # Steal long pending jobs
            # Calculate the timestamp threshold for stealing long pending jobs
            steal_threshold = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(
                seconds=settings.process_queue.job_timeout.total_seconds() + 60,
            )
            statement = (
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING.name,
                    models.Queue.started_at < steal_threshold,
                )
                .values(status=models.JobStatus.NEW.name)
            )
            result = await session.execute(statement)
            affected_rows = result.rowcount  # type: ignore[attr-defined]
            if affected_rows:
                _LOGGER.warning(
                    "Steal %i long pending jobs",
                    affected_rows,
                )
            _LOGGER.debug(
                "Process one job (max priority: %i)",
                max_priority,
            )
            await session.commit()
            return True

        await _process_one_job(job, session, make_pending, max_priority)

        return False


async def _process_one_job(
    job: models.Queue,
    session: sqlalchemy.ext.asyncio.AsyncSession,
    make_pending: bool,
    max_priority: int,
) -> None:
    sentry_sdk.set_context(
        "job",
        {"id": job.id, "event": job.module_event_name, "module": job.module or "-"},
    )

    # Capture_logs
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    handler = _Handler(job.id)
    handler.setFormatter(
        _Formatter("%(levelname)-5.5s %(pathname)s:%(lineno)d %(funcName)s()"),
    )

    module_data_formatted = utils.format_json(job.module_event_data)
    event_data_formatted = utils.format_json(job.github_event_data)
    message = module_utils.HtmlMessage(
        f"<p>module data:</p>{module_data_formatted}<p>event data:</p>{event_data_formatted}",
    )
    message.title = f"Start process job {job.module}: {job.module_event_name} - id: {job.id}, on {job.owner}/{job.repository}, with priority: {job.priority}, on application: {job.application}"
    root_logger.addHandler(handler)
    _LOGGER.info(message)
    _RUNNING_JOBS[job.id] = _JobInfo(
        job.module or "-",
        job.module_event_name,
        job.repository,
        job.priority,
        max_priority,
    )
    root_logger.removeHandler(handler)

    if make_pending:
        _LOGGER.info("Make job ID %s pending", job.id)
        job.status_enum = models.JobStatus.PENDING
        job.started_at = datetime.datetime.now(tz=datetime.UTC)
        await session.commit()
        await session.refresh(job)
        _LOGGER.debug("Process one job (max priority: %i): Make pending", max_priority)
        return

    try:
        job.status_enum = models.JobStatus.PENDING
        job.started_at = datetime.datetime.now(tz=datetime.UTC)
        await session.commit()
        await session.refresh(job)
        pending_count = await session.scalar(
            sqlalchemy.select(sqlalchemy.func.count())  # pylint: disable=not-callable
            .select_from(models.Queue)
            .where(models.Queue.status == models.JobStatus.PENDING.name),
        )
        assert pending_count is not None
        _NB_JOBS.labels(models.JobStatus.PENDING.name).set(pending_count)

        success = True
        if not job.module:
            if job.module_event_name == "dashboard":
                success = await _validate_job(
                    job.application,
                    job.github_event_data,
                )
                if success:
                    _LOGGER.info("Process dashboard issue %i", job.id)
                    await _process_dashboard_issue(
                        session,
                        job.github_event_data,
                        job.application,
                        job.owner,
                        job.repository,
                    )
                    job.status_enum = models.JobStatus.DONE
                else:
                    job.status_enum = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            else:
                _LOGGER.error("Unknown event name: %s", job.module_event_name)
                job.status_enum = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
                success = False
        else:
            success = await _validate_job(
                job.application,
                job.github_event_data,
            )
            if success:
                success = await _process_job(
                    session,
                    root_logger,
                    handler,
                    job,
                )

    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception(
            "Failed to process job id: %s on module: %s.",
            job.id,
            job.module or "-",
        )
        job.log = None
    finally:
        sentry_sdk.set_context("job", {})
        if await session.run_sync(lambda _: job.status_enum == models.JobStatus.PENDING):
            _LOGGER.error("Job %s finished with pending status", job.id)
            job.status_enum = models.JobStatus.ERROR
        job.finished_at = datetime.datetime.now(tz=datetime.UTC)
        _RUNNING_JOBS.pop(job.id)
        await session.commit()

    _LOGGER.debug("Process one job (max priority: %i): Done", max_priority)


class _Run:
    def __init__(
        self,
        Session: sqlalchemy.ext.asyncio.async_sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.ext.asyncio.AsyncSession
        ],
        return_when_empty: bool,
        max_priority: int,
    ) -> None:
        self.Session = Session  # pylint: disable=invalid-name
        self.end_when_empty = return_when_empty
        self.max_priority = max_priority

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        empty_thread_sleep = settings.process_queue.empty_thread_sleep.total_seconds()

        while True:
            global _LAST_RUN_TIME  # noqa: PLW0603
            _LAST_RUN_TIME = time.time()
            empty = True
            try:
                task = asyncio.create_task(
                    _get_process_one_job(
                        self.Session,
                        no_steal_long_pending=self.end_when_empty,
                        max_priority=self.max_priority,
                    ),
                    name="Process one job",
                )
                empty = await task
                if self.end_when_empty and empty:
                    return
            except TimeoutError:
                _LOGGER.exception("Timeout")
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to process job")

            await asyncio.sleep(empty_thread_sleep if empty else 0)


class _PrometheusWatch:
    def __init__(
        self,
        Session: sqlalchemy.ext.asyncio.async_sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.ext.asyncio.AsyncSession
        ],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.Session = Session  # pylint: disable=invalid-name
        self.last_run = time.time()
        self.loop = loop

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        current_task = asyncio.current_task()
        if current_task is not None:
            current_task.set_name("PrometheusWatch")
        await self._watch()

    async def _watch(self) -> None:
        cont = 0
        while True:
            if time.time() - _LAST_RUN_TIME > 60:
                running_task_thread = []
                for task in asyncio.all_tasks(self.loop):
                    if task.get_coro().cr_running:  # type: ignore[union-attr]
                        running_task_thread.append(f"= {task.get_name()} ")

                        frames: list[types.FrameType] = []
                        frame: types.FrameType | None = task.get_coro().cr_frame  # type: ignore[union-attr]

                        while frame is not None:
                            frames.append(frame)
                            frame = frame.f_back

                        stack = [
                            inspect.FrameInfo(
                                frame,
                                frame.f_code.co_filename,
                                frame.f_lineno,
                                frame.f_code.co_name,
                                None,
                                None,
                            )
                            for frame in reversed(frames)
                        ]

                        if stack:
                            for frame_info in stack:
                                filename = frame_info.filename
                                filename = filename.removeprefix("/app/")
                                running_task_thread.append(
                                    f'  File "{filename}", line {frame_info.lineno}, in {frame_info.function}',
                                )
                                if frame_info.code_context:
                                    running_task_thread.append(
                                        f"    {frame_info.code_context[0].strip()}",
                                    )

                running_task_thread = (
                    ["== Running tasks trace ==", *running_task_thread] if running_task_thread else []
                )

                event_loop_stack = []
                for thread in threading.enumerate():
                    if thread.name == "MainThread":
                        for (
                            thread_id,
                            frame,
                        ) in sys._current_frames().items():  # pylint: disable=protected-access
                            if thread_id == thread.ident:
                                event_loop_stack = [
                                    f"== Event loop thread stack trace '{thread.name}' ==",
                                ]

                                frames = []

                                frame1: types.FrameType | None = frame
                                while frame1 is not None:
                                    frames.append(frame1)
                                    frame1 = frame1.f_back

                                for frame2 in frames:
                                    filename = frame2.f_code.co_filename
                                    filename = filename.removeprefix("/app/")
                                    event_loop_stack.append(
                                        f'  File "{filename}", line {frame2.f_lineno}, in {frame2.f_code.co_name}',
                                    )

                log_message = (
                    [
                        "Prometheus watch: alive",
                        "== Threads ==",
                        *[str(thread) for thread in threading.enumerate()],
                        "== Tasks ==",
                        *[str(task) for task in asyncio.all_tasks(self.loop)],
                        *running_task_thread,
                        *event_loop_stack,
                    ]
                    if settings.process_queue.debug
                    else ["Prometheus watch: alive"]
                )
                _LOGGER.debug(
                    "\n    ".join(log_message),
                )
            else:
                # Log to indicate that the Prometheus monitoring system is active.
                # This branch is executed when debugging is disabled.
                _LOGGER.debug("Prometheus watch: alive")
            try:
                _NB_JOBS.labels("Tasks").set(len(asyncio.all_tasks()))
                cont += 1
                if cont % 10 == 0:
                    tasks_messages = []
                    for task in asyncio.all_tasks():
                        if not task.done():
                            tasks_messages.append(task.get_name())
                            if cont % 100 == 0:
                                string_io = io.StringIO()
                                task.print_stack(limit=5, file=string_io)
                                tasks_messages.append(string_io.getvalue())
                                tasks_messages.append("")
                    message = module_utils.HtmlMessage("<br>\n".join(tasks_messages))
                    message.title = "Running tasks"
                    _LOGGER.debug(message)
            except RuntimeError:
                pass
            async with self.Session() as session:
                for status in models.JobStatus:
                    _NB_JOBS.labels(status.name).set(
                        (
                            await session.execute(
                                sqlalchemy.select(sqlalchemy.func.count(models.Queue.id)).where(  # pylint: disable=not-callable
                                    models.Queue.status == status.name
                                ),
                            )
                        ).scalar()
                        or 0,
                    )
            text = []
            for id_, job in _RUNNING_JOBS.items():
                text.append(
                    f"{id_}: {job.module} {job.module_event_name} {job.repository} [{job.priority}] (Worker max priority {job.worker_max_priority})",
                )
            try:
                for task in asyncio.all_tasks():
                    txt = io.StringIO()
                    task.print_stack(file=txt)
                    text.append("-" * 30)
                    text.append(txt.getvalue())
            except RuntimeError as exception:
                text.append(str(exception))

            if time.time() - self.last_run > 300:
                error_message = ["Old Status"]
                async with await anyio.Path("/var/ghci/job_info").open(encoding="utf-8") as file_:
                    error_message.extend((await file_.read()).split("\n"))
                error_message.append("-" * 30)
                error_message.append("New status")
                error_message.extend(text)
                message = module_utils.HtmlMessage("<br>\n".join(error_message))
                message.title = "Too long waiting for a schedule"
                _LOGGER.error(message)
            self.last_run = time.time()

            async with await anyio.Path("/var/ghci/job_info").open("w", encoding="utf-8") as file_:
                await file_.write("\n".join(text))
                await file_.write("\n")
            await asyncio.sleep(60)


class _WatchDog:
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        current_task = asyncio.current_task()
        if current_task is not None:
            current_task.set_name("WatchDog")
        while True:
            _LOGGER.debug("Watch dog: alive")
            async with await anyio.open_file(
                "/var/ghci/watch_dog",
                "w",
                encoding="utf-8",
            ) as file_:
                await file_.write(datetime.datetime.now(datetime.UTC).isoformat())
                await file_.write("\n")
                await file_.write(datetime.datetime.now(datetime.UTC).isoformat())
                await file_.write("\n")
            await asyncio.sleep(60)


class HandleSigint:
    """Handle SIGINT."""

    def __init__(
        self,
        Session: sqlalchemy.orm.sessionmaker[sqlalchemy.orm.Session],  # noqa: N803 # # pylint: disable=unsubscriptable-object
    ) -> None:  # pylint: disable=invalid-name
        self.Session = Session  # pylint: disable=invalid-name

    def __call__(self) -> None:
        """Handle SIGINT."""
        with self.Session() as session:
            jobs_ids = _RUNNING_JOBS.keys()
            for job in session.query(models.Queue).filter(
                sqlalchemy.and_(
                    models.Queue.id.in_(jobs_ids),
                    models.Queue.status == models.JobStatus.PENDING.name,
                ),
            ):
                job.status_enum = models.JobStatus.NEW
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
        sys.exit()


async def _async_main() -> None:
    """Process the jobs present in the database queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--exit-when-empty",
        action="store_true",
        help="Exit when the queue is empty",
    )
    parser.add_argument(
        "--only-one",
        action="store_true",
        help="Exit after processing one job",
    )
    parser.add_argument(
        "--make-pending",
        action="store_true",
        help="Make one job in pending",
    )

    args = parser.parse_args()

    loop = asyncio.get_running_loop()
    with aiomonitor.start_monitor(loop):
        loop.set_default_executor(
            concurrent.futures.ThreadPoolExecutor(
                max_workers=settings.process_queue.max_workers,
            ),
        )
        loop.slow_callback_duration = settings.process_queue.slow_callback_duration.total_seconds()
        if settings.process_queue.debug:
            loop.set_debug(True)

        def do_exit(loop: asyncio.AbstractEventLoop) -> None:
            print("Exiting...")
            loop.stop()

        for signal_type in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_type, functools.partial(do_exit, loop))

        options = {}
        if settings.sqlalchemy.pool_recycle is not None:
            options["pool_recycle"] = settings.sqlalchemy.pool_recycle
        if settings.sqlalchemy.pool_size is not None:
            options["pool_size"] = settings.sqlalchemy.pool_size
        if settings.sqlalchemy.max_overflow is not None:
            options["max_overflow"] = settings.sqlalchemy.max_overflow
        async_engine = sqlalchemy.ext.asyncio.create_async_engine(settings.sqlalchemy.async_url, **options)
        engine = sqlalchemy.create_engine(settings.sqlalchemy.url)
        Session = sqlalchemy.orm.sessionmaker(  # noqa: N806
            bind=engine,
        )  # pylint: disable=invalid-name
        AsyncSession = sqlalchemy.ext.asyncio.async_sessionmaker(  # noqa: N806
            bind=async_engine,
        )  # pylint: disable=invalid-name

        # Create tables if they do not exist
        async with async_engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)

        handle_sigint = HandleSigint(Session)
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, handle_sigint)

        if args.only_one:
            await _get_process_one_job(
                AsyncSession,
                no_steal_long_pending=args.exit_when_empty,
                make_pending=args.make_pending,
            )
            sys.exit(0)
        if args.make_pending:
            await _get_process_one_job(
                AsyncSession,
                no_steal_long_pending=args.exit_when_empty,
                make_pending=True,
            )
            sys.exit(0)

        if not args.exit_when_empty and c2casgiutils.config.settings.prometheus.port:

            class LogHandler(
                prometheus_client.exposition._SilentHandler,  # noqa: SLF001
            ):  # pylint: disable=protected-access
                """WSGI handler that does not log requests."""

                def log_message(self, *args: Any) -> None:
                    _LOGGER_WSGI.debug(*args)

            prometheus_client.exposition._SilentHandler = LogHandler  # type: ignore[misc] # pylint: disable=protected-access

            prometheus_client.start_http_server(c2casgiutils.config.settings.prometheus.port)

        priority_groups = [int(e) for e in settings.process_queue.priority_groups.split(",")]

        tasks = []
        if not args.exit_when_empty:
            tasks.append(asyncio.create_task(_WatchDog()(), name="Watch Dog"))
            tasks.append(
                asyncio.create_task(
                    _PrometheusWatch(AsyncSession, loop)(),
                    name="Prometheus Watch",
                ),
            )

        tasks.extend(
            [
                asyncio.create_task(
                    _Run(AsyncSession, args.exit_when_empty, priority)(),
                    name=f"Run ({priority})",
                )
                for priority in priority_groups
            ],
        )
        await asyncio.gather(*tasks)


def main() -> None:
    """Process the jobs present in the database queue."""
    socket.setdefaulttimeout(settings.process_queue.socket_timeout.total_seconds())
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
