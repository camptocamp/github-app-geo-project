"""Process the jobs present in the database queue."""

import argparse
import asyncio
import concurrent
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
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import aiofiles
import aiomonitor
import c2cwsgiutils.setup_process
import githubkit.exception
import githubkit.versions.latest.models
import githubkit.versions.v2022_11_28.webhooks.issues
import githubkit.webhooks
import plaster
import prometheus_client.exposition
import sentry_sdk
import sqlalchemy.ext.asyncio
import sqlalchemy.orm
from prometheus_client import Gauge

from github_app_geo_project import configuration, models, module, project_configuration, utils
from github_app_geo_project.module import GHCIError, modules
from github_app_geo_project.module import utils as module_utils

if TYPE_CHECKING:
    import types

_LOGGER = logging.getLogger(__name__)
_LOGGER_WSGI = logging.getLogger("prometheus_client.wsgi")

_NB_JOBS = Gauge("ghci_jobs_number", "Number of jobs", ["status"])
_MODULE_STATUS_LOCK: dict[str, asyncio.Lock] = {}


class _JobInfo(NamedTuple):
    module: str
    event_name: str
    repository: str
    priority: int
    worker_max_priority: int


_RUNNING_JOBS: dict[int, _JobInfo] = {}

_LAST_RUN_TIME = time.time()


class _Handler(logging.Handler):
    context_var: contextvars.ContextVar[int] = contextvars.ContextVar("job_id")

    def __init__(self, job_id: int) -> None:
        super().__init__()
        self.results: list[logging.LogRecord] = []
        self.job_id = job_id
        self.context_var.set(job_id)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if self.context_var.get() != self.job_id:
                return
        except LookupError:
            return
        if isinstance(record.msg, module_utils.Message):
            record.msg = record.msg.to_html(style="collapse")
        self.results.append(record)


class _Formatter(logging.Formatter):
    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802
        str_msg = super().formatMessage(record).strip()
        styles = []
        if record.levelname == "WARNING":
            styles.append("color: orange")
        elif record.levelname == "ERROR":
            styles.append("color: red")
        elif record.levelname == "CRITICAL":
            styles.append("color: red")
            styles.append("font-weight: bold")
        elif record.levelname == "INFO":
            styles.append("color: rgba(var(--bs-link-color-rgb)")
        attributes = f' style="{"; ".join(styles)}"' if styles else ""
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


async def _validate_job(config: dict[str, Any], application: str, event_data: dict[str, Any]) -> bool:
    if "TEST_APPLICATION" in os.environ:
        return True
    github_application = await configuration.get_github_application(config, application)
    installation_id = event_data.get("installation", {}).get("id", 0)
    if github_application.id == installation_id:
        _LOGGER.error("Invalid installation id %i != %i", github_application.id, installation_id)
        return False
    return True


async def _process_job(
    config: dict[str, str],
    session: sqlalchemy.ext.asyncio.AsyncSession,
    root_logger: logging.Logger,
    handler: _Handler,
    job: models.Queue,
) -> bool:
    current_module = modules.MODULES.get(job.module)
    if current_module is None:
        _LOGGER.error("Unknown module %s", job.module)
        return False

    logs_url = config["service-url"]
    logs_url = logs_url if logs_url.endswith("/") else logs_url + "/"
    logs_url = urllib.parse.urljoin(logs_url, "logs/")
    logs_url = urllib.parse.urljoin(logs_url, str(job.id))

    new_issue_data = None
    issue_data = ""
    module_config: project_configuration.ModuleConfiguration = {}
    github_project: configuration.GithubProject | None = None
    check_run: githubkit.versions.latest.models.CheckRun | None = None
    tasks: list[asyncio.Task[Any]] = []
    if "TEST_APPLICATION" not in os.environ:
        github_application = await configuration.get_github_application(config, job.application)
        if job.owner is not None and job.repository is not None:
            github_project = await configuration.get_github_project(
                config,
                github_application,
                job.owner,
                job.repository,
            )
            # Get Rate limit status
            rate_limit = (await github_project.aio_github.rest.rate_limit.async_get()).parsed_data
            if rate_limit.rate.remaining < 1000:
                _LOGGER.warning(
                    "Rate limit status: %s/%s",
                    rate_limit.rate.remaining,
                    rate_limit.rate.limit,
                )
                # Wait until github_project.github.rate_limiting_resettime
                await asyncio.sleep(
                    max(
                        0,
                        rate_limit.rate.reset - time.time(),
                    ),
                )

            if current_module.required_issue_dashboard():
                dashboard_issue = await _get_dashboard_issue(github_project)
                if dashboard_issue:
                    issue_full_data = dashboard_issue.body
                    assert isinstance(issue_full_data, str)
                    issue_data = utils.get_dashboard_issue_module(issue_full_data, job.module)

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
                deprecated_repo=None,
                aio_installation=None,
                aio_github=None,
                aio_repo=None,
            )

    if module_config.get("enabled", project_configuration.MODULE_ENABLED_DEFAULT):
        try:
            if "TEST_APPLICATION" not in os.environ:
                if job.check_run_id is None and job.owner is not None and job.repository is not None:
                    check_run = await module_utils.create_checks(
                        job,
                        session,
                        current_module,
                        github_project,
                        config["service-url"],
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
                event_name=job.event_name,
                event_data=job.event_data,
                module_config=current_module.configuration_from_json(cast("dict[str, Any]", module_config)),
                module_event_data=current_module.event_data_from_json(job.module_data),
                issue_data=issue_data,
                job_id=job.id,
                service_url=config["service-url"],
            )
            root_logger.addHandler(handler)
            result = None
            try:
                start = datetime.datetime.now(tz=datetime.UTC)
                job_timeout = int(os.environ.get("GHCI_JOB_TIMEOUT", str(50 * 60)))
                transversal_status = None
                async with asyncio.timeout(job_timeout):
                    task = asyncio.create_task(
                        current_module.process(context),
                        name=f"Process Job {job.id} - {job.event_name} - {job.module or '-'}",
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
                                module_status.data if module_status is not None else None,
                            )
                            transversal_status = await current_module.update_transversal_status(
                                context,
                                result.intermediate_status,
                                transversal_status,
                            )
                            if transversal_status is not None:
                                root_logger.removeHandler(handler)
                                _LOGGER.debug(
                                    "Update module status %s `%s` (job id: %i, type: %s, %s)\n%s",
                                    job.module,
                                    current_module.title(),
                                    job.id,
                                    type(transversal_status),
                                    transversal_status,
                                    current_module.transversal_status_to_json(transversal_status),
                                )
                                if module_status is None:
                                    module_status = models.ModuleStatus(
                                        module=job.module,
                                        data=current_module.transversal_status_to_json(transversal_status),
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
                _LOGGER.debug("Module %s took %s", job.module, datetime.datetime.now(tz=datetime.UTC) - start)

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
                                ", ".join([a.title or "Untitled" for a in result.actions]),
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
                _LOGGER.exception("Failed to process job id: %s on module: %s", job.id, job.module)
                raise GHCIError(str(exception)) from exception
            finally:
                root_logger.removeHandler(handler)

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
                                conclusion="success" if result is None or result.success else "failure",
                                output={
                                    "title": check_output.get("title", current_module.title()),
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
                    _LOGGER.exception("Timeout while updating check run %s", job.check_run_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    _LOGGER.exception(
                        "Failed to update check run %s",
                        job.check_run_id,
                    )

            await session.refresh(job)
            job.status_enum = (
                models.JobStatus.DONE if result is None or result.success else models.JobStatus.ERROR
            )
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)

            job.log = "\n".join([handler.format(msg) for msg in handler.results])
            if result is not None and github_project is not None and github_project.aio_github is not None:
                _LOGGER.debug("Process actions")
                for action in result.actions:
                    new_job = models.Queue()
                    new_job.priority = action.priority if action.priority >= 0 else job.priority
                    new_job.application = job.application
                    new_job.owner = job.owner
                    new_job.repository = job.repository
                    new_job.event_name = action.title or job.event_name
                    new_job.event_data = job.event_data
                    new_job.module = job.module
                    new_job.module_data = current_module.event_data_to_json(action.data)
                    session.add(new_job)
                    await module_utils.create_checks(
                        new_job,
                        session,
                        current_module,
                        github_project,
                        config["service-url"],
                    )
                await session.commit()
                await session.refresh(job)

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
            assert check_run is not None
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
                None if isinstance(proc_error, subprocess.TimeoutExpired) else proc_error.returncode,
                proc_error.output,
                cast("str", proc_error.stderr),
            )
            message.title = f"Error process job '{job.id}' on module: {job.module}"
            root_logger.addHandler(handler)
            try:
                _LOGGER.exception(message)
            finally:
                root_logger.removeHandler(handler)
            assert check_run is not None
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
                    _LOGGER.exception("Failed to process job id: %s on module: %s", job.id, job.module)
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
                    event_name="event",
                    event_data=job.event_data,
                    module_data=job.module_data,
                ),
            )
        except Exception:
            _LOGGER.exception(
                "Failed to cleanup job id: %s on module: %s, module data:\n%s\nevent data:\n%s",
                job.id,
                job.module,
                job.module_data,
                job.event_data,
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
            _LOGGER.debug("Update issue %s, with:\n%s", dashboard_issue.number, issue_full_data)
            if github_project is not None:
                await github_project.aio_github.rest.issues.async_update(
                    owner=job.owner,
                    repo=job.repository,
                    issue_number=dashboard_issue.number,
                    body=issue_full_data,
                )
        elif new_issue_data and os.environ.get("GHCI_CREATE_DASHBOARD_ISSUE", "1").lower() in (
            "1",
            "true",
            "on",
        ):
            issue_full_data = utils.update_dashboard_issue_module(
                f"This issue is the dashboard used by GHCI modules.\n\n[Project on GHCI]({config['service-url']}project/{job.owner}/{job.repository})\n\n",
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
) -> githubkit.versions.latest.models.Issue | None:
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
    config: dict[str, Any],
    session: sqlalchemy.ext.asyncio.AsyncSession,
    event_data: dict[str, Any],
    application: str,
    owner: str,
    repository: str,
) -> None:
    """Process changes on the dashboard issue."""
    github_application = await configuration.get_github_application(config, application)
    github_project = await configuration.get_github_project(config, github_application, owner, repository)
    event_data_issue = githubkit.webhooks.parse_obj("issues", event_data)

    if not isinstance(event_data_issue, githubkit.versions.v2022_11_28.webhooks.issues.WebhookIssuesEdited):  # type: ignore[attr-defined]
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

            for name in config.get(f"application.{github_project.application.name}.modules", "").split():
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
                                event_name="dashboard",
                                event_data={
                                    "type": "dashboard",
                                    "old_data": module_old,
                                    "new_data": module_new,
                                },
                                owner=github_project.owner,
                                repository=github_project.repository,
                                github_application=github_project.application,
                            ),
                        ):
                            job = models.Queue()
                            job.priority = (
                                action.priority if action.priority >= 0 else module.PRIORITY_DASHBOARD
                            )
                            job.application = github_project.application.name
                            job.owner = github_project.owner
                            job.repository = github_project.repository
                            job.event_name = action.title or "dashboard"
                            job.event_data = {
                                "type": "dashboard",
                                "old_data": module_old,
                                "new_data": module_new,
                            }
                            job.module = name
                            job.module_data = current_module.event_data_to_json(action.data)
                            session.add(job)
                            await session.flush()
                            if action.checks:
                                await module_utils.create_checks(
                                    job,
                                    session,
                                    current_module,
                                    github_project,
                                    config["service-url"],
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
    config: dict[str, Any],
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
                _LOGGER.debug("Process one job (max priority: %i): No job to process", max_priority)
                return True

            # Very long pending job => error
            await session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING.name,
                    models.Queue.created_at
                    < datetime.datetime.now(tz=datetime.UTC)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT_ERROR", "86400"))),
                )
                .values(status=models.JobStatus.ERROR.name),
            )

            # Get too old pending jobs
            await session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING.name,
                    models.Queue.started_at
                    < datetime.datetime.now(tz=datetime.UTC)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT", "3600")) + 60),
                )
                .values(status=models.JobStatus.NEW.name),
            )

            _LOGGER.debug("Process one job (max priority: %i): Steal long pending job", max_priority)
            return True

        await _process_one_job(job, session, config, make_pending, max_priority)

        return False


async def _process_one_job(
    job: models.Queue,
    session: sqlalchemy.ext.asyncio.AsyncSession,
    config: dict[str, Any],
    make_pending: bool,
    max_priority: int,
) -> None:
    sentry_sdk.set_context("job", {"id": job.id, "event": job.event_name, "module": job.module or "-"})

    # Capture_logs
    root_logger = logging.getLogger()
    handler = _Handler(job.id)
    handler.setFormatter(_Formatter("%(levelname)-5.5s %(pathname)s:%(lineno)d %(funcName)s()"))

    module_data_formatted = utils.format_json(job.module_data)
    event_data_formatted = utils.format_json(job.event_data)
    message = module_utils.HtmlMessage(
        f"<p>module data:</p>{module_data_formatted}<p>event data:</p>{event_data_formatted}",
    )
    message.title = f"Start process job {job.module}: {job.event_name} - id: {job.id}, on {job.owner}/{job.repository}, with priority: {job.priority}, on application: {job.application}"
    root_logger.addHandler(handler)
    _LOGGER.info(message)
    _RUNNING_JOBS[job.id] = _JobInfo(
        job.module or "-",
        job.event_name,
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
            if job.event_name == "dashboard":
                success = await _validate_job(config, job.application, job.event_data)
                if success:
                    _LOGGER.info("Process dashboard issue %i", job.id)
                    await _process_dashboard_issue(
                        config,
                        session,
                        job.event_data,
                        job.application,
                        job.owner,
                        job.repository,
                    )
                    job.status_enum = models.JobStatus.DONE
                else:
                    job.status_enum = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            else:
                _LOGGER.error("Unknown event name: %s", job.event_name)
                job.status_enum = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
                success = False
        else:
            success = await _validate_job(config, job.application, job.event_data)
            if success:
                success = await _process_job(
                    config,
                    session,
                    root_logger,
                    handler,
                    job,
                )

    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Failed to process job id: %s on module: %s.", job.id, job.module or "-")
        job.log = "\n".join([handler.format(msg) for msg in handler.results])
    finally:
        sentry_sdk.set_context("job", {})
        if job.status_enum == models.JobStatus.PENDING:
            _LOGGER.error("Job %s finished with pending status", job.id)
            job.status_enum = models.JobStatus.ERROR
        job.finished_at = datetime.datetime.now(tz=datetime.UTC)
        _RUNNING_JOBS.pop(job.id)
        await session.commit()

    _LOGGER.debug("Process one job (max priority: %i): Done", max_priority)


class _Run:
    def __init__(
        self,
        config: dict[str, Any],
        Session: sqlalchemy.ext.asyncio.async_sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.ext.asyncio.AsyncSession
        ],
        return_when_empty: bool,
        max_priority: int,
    ) -> None:
        self.config = config
        self.Session = Session  # pylint: disable=invalid-name
        self.end_when_empty = return_when_empty
        self.max_priority = max_priority

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        empty_thread_sleep = int(os.environ.get("GHCI_EMPTY_THREAD_SLEEP", "10"))

        while True:
            global _LAST_RUN_TIME  # pylint: disable=global-statement
            _LAST_RUN_TIME = time.time()
            empty = True
            try:
                task = asyncio.create_task(
                    _get_process_one_job(
                        self.config,
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
        Session: sqlalchemy.orm.sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.orm.Session
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
        await asyncio.to_thread(self._watch)

    def _watch(self) -> None:
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
                                    running_task_thread.append(f"    {frame_info.code_context[0].strip()}")

                running_task_thread = (
                    ["== Running tasks trace ==", *running_task_thread] if running_task_thread else []
                )

                event_loop_stack = []
                for thread in threading.enumerate():
                    if thread.name == "MainThread":
                        for thread_id, frame in sys._current_frames().items():  # pylint: disable=protected-access
                            if thread_id == thread.ident:
                                event_loop_stack = [f"== Event loop thread stack trace '{thread.name}' =="]

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
                    if os.environ.get("GHCI_DEBUG", "0").lower() in ("1", "true", "on")
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
            with self.Session() as session:
                for status in models.JobStatus:
                    _NB_JOBS.labels(status.name).set(
                        session.query(models.Queue).filter(models.Queue.status == status.name).count(),
                    )
            text = []
            for id_, job in _RUNNING_JOBS.items():
                text.append(
                    f"{id_}: {job.module} {job.event_name} {job.repository} [{job.priority}] (Worker max priority {job.worker_max_priority})",
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
                with Path("/var/ghci/job_info").open(encoding="utf-8") as file_:
                    error_message.extend(file_.read().split("\n"))
                error_message.append("-" * 30)
                error_message.append("New status")
                error_message.extend(text)
                message = module_utils.HtmlMessage("<br>\n".join(error_message))
                message.title = "Too long waiting for a schedule"
                _LOGGER.error(message)
            self.last_run = time.time()

            with Path("/var/ghci/job_info").open("w", encoding="utf-8") as file_:
                file_.write("\n".join(text))
                file_.write("\n")
            time.sleep(60)


class _WatchDog:
    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        current_task = asyncio.current_task()
        if current_task is not None:
            current_task.set_name("WatchDog")
        while True:
            _LOGGER.debug("Watch dog: alive")
            async with aiofiles.open("/var/ghci/watch_dog", "w", encoding="utf-8") as file_:
                await file_.write(datetime.datetime.now(datetime.UTC).isoformat())
                await file_.write("\n")
                await file_.write(datetime.datetime.now(datetime.UTC).isoformat())
                await file_.write("\n")
            await asyncio.sleep(60)


class HandleSigint:
    """Handle SIGINT."""

    def __init__(self, Session: sqlalchemy.orm.sessionmaker[sqlalchemy.orm.Session]) -> None:  # pylint: disable=invalid-name,unsubscriptable-object
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
    parser.add_argument("--exit-when-empty", action="store_true", help="Exit when the queue is empty")
    parser.add_argument("--only-one", action="store_true", help="Exit after processing one job")
    parser.add_argument("--make-pending", action="store_true", help="Make one job in pending")
    c2cwsgiutils.setup_process.fill_arguments(parser)

    args = parser.parse_args()

    c2cwsgiutils.setup_process.init(args.config_uri)

    loop = asyncio.get_running_loop()
    with aiomonitor.start_monitor(loop):
        loop.set_default_executor(
            concurrent.futures.ThreadPoolExecutor(max_workers=int(os.environ.get("GHCI_MAX_WORKERS", "2"))),
        )
        loop.slow_callback_duration = float(
            os.environ.get("GHCI_SLOW_CALLBACK_DURATION", "60"),
        )  # 1 minute by default
        if os.environ.get("GHCI_DEBUG", "0").lower() in ("1", "true", "on"):
            loop.set_debug(True)

        def do_exit(loop: asyncio.AbstractEventLoop) -> None:
            print("Exiting...")
            loop.stop()

        for signal_type in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signal_type, functools.partial(do_exit, loop))

        loader = plaster.get_loader(args.config_uri)
        config = loader.get_settings("app:app")
        options = {key[len("sqlalchemy.") :]: config[key] for key in config if key.startswith("sqlalchemy.")}
        url = options.pop("url")
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        for key in ("pool_recycle", "pool_size", "max_overflow"):
            if key in options:
                options[key] = int(options[key])
        async_engine = sqlalchemy.ext.asyncio.create_async_engine(url, **options)
        engine = sqlalchemy.engine_from_config(config, "sqlalchemy.")
        Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name
        AsyncSession = sqlalchemy.ext.asyncio.async_sessionmaker(bind=async_engine)  # pylint: disable=invalid-name

        # Create tables if they do not exist
        async with async_engine.begin() as connection:
            await connection.run_sync(models.Base.metadata.create_all)

        handle_sigint = HandleSigint(Session)
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, handle_sigint)

        if args.only_one:
            await _get_process_one_job(
                config,
                AsyncSession,
                no_steal_long_pending=args.exit_when_empty,
                make_pending=args.make_pending,
            )
            sys.exit(0)
        if args.make_pending:
            await _get_process_one_job(
                config,
                AsyncSession,
                no_steal_long_pending=args.exit_when_empty,
                make_pending=True,
            )
            sys.exit(0)

        if not args.exit_when_empty and "C2C_PROMETHEUS_PORT" in os.environ:

            class LogHandler(prometheus_client.exposition._SilentHandler):  # pylint: disable=protected-access
                """WSGI handler that does not log requests."""

                def log_message(self, *args: Any) -> None:
                    _LOGGER_WSGI.debug(*args)

            prometheus_client.exposition._SilentHandler = LogHandler  # type: ignore[misc] # pylint: disable=protected-access

            prometheus_client.start_http_server(int(os.environ["C2C_PROMETHEUS_PORT"]))

        priority_groups = [int(e) for e in os.environ.get("GHCI_PRIORITY_GROUPS", "2147483647").split(",")]

        tasks = []
        if not args.exit_when_empty:
            tasks.append(asyncio.create_task(_WatchDog()(), name="Watch Dog"))
            tasks.append(asyncio.create_task(_PrometheusWatch(Session, loop)(), name="Prometheus Watch"))

        tasks.extend(
            [
                asyncio.create_task(
                    _Run(config, AsyncSession, args.exit_when_empty, priority)(),
                    name=f"Run ({priority})",
                )
                for priority in priority_groups
            ],
        )
        await asyncio.gather(*tasks)


def main() -> None:
    """Process the jobs present in the database queue."""
    socket.setdefaulttimeout(int(os.environ.get("GHCI_SOCKET_TIMEOUT", "120")))
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
