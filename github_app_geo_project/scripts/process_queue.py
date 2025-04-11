"""Process the jobs present in the database queue."""

import argparse
import asyncio
import contextvars
import datetime
import functools
import io
import logging
import os
import signal
import socket
import subprocess  # nosec
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, NamedTuple, cast

import aiofiles
import c2cwsgiutils.setup_process
import github
import plaster
import prometheus_client.exposition
import sentry_sdk
import sqlalchemy.orm
from prometheus_client import Gauge

from github_app_geo_project import configuration, models, module, project_configuration, utils
from github_app_geo_project.module import GHCIError, modules
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.views import webhook

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


def _validate_job(config: dict[str, Any], application: str, event_data: dict[str, Any]) -> bool:
    if "TEST_APPLICATION" in os.environ:
        return True
    github_application = configuration.get_github_application(config, application)
    github_app = github_application.integration.get_app()
    installation_id = event_data.get("installation", {}).get("id", 0)
    if github_app.id == installation_id:
        _LOGGER.error("Invalid installation id %i != %i", github_app.id, installation_id)
        return False
    return True


async def _process_job(
    config: dict[str, str],
    session: sqlalchemy.orm.Session,
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
    check_run: github.CheckRun.CheckRun | None = None
    if "TEST_APPLICATION" not in os.environ:
        github_application = configuration.get_github_application(config, job.application)
        github_project = configuration.get_github_project(
            config,
            github_application,
            job.owner,
            job.repository,
        )
        # Get Rate limit status
        if github_project.github.rate_limiting[0] < 1000:
            _LOGGER.warning(
                "Rate limit status: %s",
                github_project.github.rate_limiting,
            )
            # Wait until github_project.github.rate_limiting_resettime
            await asyncio.sleep(
                max(
                    0,
                    github_project.github.rate_limiting_resettime - time.time(),
                ),
            )

        repo = github_project.repo

        if current_module.required_issue_dashboard():
            dashboard_issue = _get_dashboard_issue(github_application, repo)
            if dashboard_issue:
                issue_full_data = dashboard_issue.body
                issue_data = utils.get_dashboard_issue_module(issue_full_data, job.module)

        module_config = cast(
            "project_configuration.ModuleConfiguration",
            configuration.get_configuration(config, job.owner, job.repository, job.application).get(
                job.module,
                {},
            ),
        )
        if job.check_run_id is not None:
            check_run = repo.get_check_run(job.check_run_id)
    if module_config.get("enabled", project_configuration.MODULE_ENABLED_DEFAULT):
        try:
            if "TEST_APPLICATION" not in os.environ:
                if job.check_run_id is None:
                    check_run = webhook.create_checks(
                        job,
                        session,
                        current_module,
                        github_project.repo,
                        job.event_data,
                        config["service-url"],
                    )

                assert check_run is not None
                check_run.edit(external_id=str(job.id), status="in_progress", details_url=logs_url)

            # Close transaction if one is open
            session.commit()
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
                    result = await current_module.process(context)
                    if result.updated_transversal_status:
                        if job.module not in _MODULE_STATUS_LOCK:
                            _MODULE_STATUS_LOCK[job.module] = asyncio.Lock()
                        async with _MODULE_STATUS_LOCK[job.module]:
                            root_logger.removeHandler(handler)
                            module_status = (
                                session.query(models.ModuleStatus)
                                .filter(models.ModuleStatus.module == job.module)
                                .with_for_update(of=models.ModuleStatus)
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
                                    session.execute(
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

                _LOGGER.debug("Module %s took %s", job.module, datetime.datetime.now(tz=datetime.UTC) - start)

                if result is not None:
                    non_none = [
                        *(["dashboard"] if result.dashboard is not None else []),
                        *(["transversal_status"] if transversal_status is not None else []),
                        *(["actions"] if result.actions else []),
                        *(["output"] if result.output is not None else []),
                    ]
                    if non_none:
                        _LOGGER.info(
                            "Module %s finished with: %s",
                            job.module,
                            ", ".join(non_none),
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
                _LOGGER.exception("Failed to process job id: %s on module: %s", job.id, job.module)
                raise GHCIError(str(exception)) from exception
            finally:
                root_logger.removeHandler(handler)

            if github_project is not None:
                _LOGGER.debug("Update check run %s", job.check_run_id)
                check_output = {
                    "title": current_module.title(),
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
                    check_run.edit(
                        status="completed",
                        conclusion="success" if result is None or result.success else "failure",
                        output=check_output,
                    )
                except github.GithubException as exception:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        exception.data,
                        (
                            "\n".join(f"{k}: {v}" for k, v in exception.headers.items())
                            if exception.headers
                            else ""
                        ),
                        exception.message,
                        exception.status,
                    )
                    raise
            job.status = models.JobStatus.DONE if result is None or result.success else models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)

            job.log = "\n".join([handler.format(msg) for msg in handler.results])
            if result is not None:
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
                    webhook.create_checks(
                        new_job,
                        session,
                        current_module,
                        repo,
                        job.event_data,
                        config["service-url"],
                        action.title,
                    )

                    session.add(new_job)
            session.commit()
            new_issue_data = result.dashboard if result is not None else None
        except github.GithubException as exception:
            job.status = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            root_logger.addHandler(handler)
            try:
                _LOGGER.exception(
                    "Failed to process job id: %s on module: %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                    job.id,
                    job.module,
                    exception.data,
                    (
                        "\n".join(f"{k}: {v}" for k, v in exception.headers.items())
                        if exception.headers
                        else ""
                    ),
                    exception.message,
                    exception.status,
                )
            finally:
                root_logger.removeHandler(handler)
            assert check_run is not None
            try:
                check_run.edit(
                    status="completed",
                    conclusion="failure",
                    output={
                        "title": current_module.title(),
                        "summary": f"Unexpected error: {exception}\n[See logs for more details]({logs_url}))",
                    },
                )
            except github.GithubException as github_exception:
                root_logger.addHandler(handler)
                try:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        github_exception.data,
                        (
                            "\n".join(f"{k}: {v}" for k, v in github_exception.headers.items())
                            if github_exception.headers
                            else ""
                        ),
                        github_exception.message,
                        github_exception.status,
                    )
                finally:
                    root_logger.removeHandler(handler)
            raise
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as proc_error:
            job.status = models.JobStatus.ERROR
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
                check_run.edit(
                    status="completed",
                    conclusion="failure",
                    output={
                        "title": current_module.title(),
                        "summary": f"Unexpected error: {proc_error}\n[See logs for more details]({logs_url}))",
                    },
                )
            except github.GithubException as exception:
                _LOGGER.exception(
                    "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                    job.check_run_id,
                    exception.data,
                    (
                        "\n".join(f"{k}: {v}" for k, v in exception.headers.items())
                        if exception.headers
                        else ""
                    ),
                    exception.message,
                    exception.status,
                )
            raise
        except Exception as exception:
            job.status = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            if not isinstance(exception, GHCIError):
                root_logger.addHandler(handler)
                try:
                    _LOGGER.exception("Failed to process job id: %s on module: %s", job.id, job.module)
                finally:
                    root_logger.removeHandler(handler)
            if check_run is not None:
                try:
                    check_run.edit(
                        status="completed",
                        conclusion="failure",
                        output={
                            "title": current_module.title(),
                            "summary": f"Unexpected error: {exception}\n[See logs for more details]({logs_url}))",
                        },
                    )
                except github.GithubException as github_exception:
                    _LOGGER.exception(
                        "Failed to update check run %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                        job.check_run_id,
                        github_exception.data,
                        (
                            "\n".join(f"{k}: {v}" for k, v in github_exception.headers.items())
                            if github_exception.headers
                            else ""
                        ),
                        github_exception.message,
                        github_exception.status,
                    )
            raise
    else:
        try:
            _LOGGER.info("Module %s is disabled", job.module)
            job.status = models.JobStatus.SKIPPED
            if check_run is not None:
                check_run.edit(
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

    if current_module.required_issue_dashboard() and new_issue_data is not None:
        dashboard_issue = _get_dashboard_issue(github_application, repo)

        if dashboard_issue:
            issue_full_data = utils.update_dashboard_issue_module(
                dashboard_issue.body,
                job.module,
                current_module,
                new_issue_data,
            )
            _LOGGER.debug("Update issue %s, with:\n%s", dashboard_issue.number, issue_full_data)
            dashboard_issue.edit(body=issue_full_data)
        elif new_issue_data:
            issue_full_data = utils.update_dashboard_issue_module(
                f"This issue is the dashboard used by GHCI modules.\n\n[Project on GHCI]({config['service-url']}project/{job.owner}/{job.repository})\n\n",
                job.module,
                current_module,
                new_issue_data,
            )
            repo.create_issue(
                f"{github_application.integration.get_app().name} Dashboard",
                issue_full_data,
            )
    return True


def _process_event(
    config: dict[str, str],
    event_data: dict[str, str],
    session: sqlalchemy.orm.Session,
) -> None:
    for application in config["applications"].split():
        _LOGGER.info("Process the event: %s, application: %s", event_data.get("name"), application)

        if "TEST_APPLICATION" in os.environ:
            webhook.process_event(
                webhook.ProcessContext(
                    owner="camptocamp",
                    repository="test",
                    config=config,
                    application=os.environ["TEST_APPLICATION"],
                    event_name="event",
                    event_data=event_data,
                    session=session,
                    github_application=None,  # type: ignore[arg-type]
                    service_url=config["service-url"],
                ),
            )
        else:
            github_application = configuration.get_github_application(config, application)
            for installation in github_application.integration.get_installations():
                for repo in installation.get_repos():
                    webhook.process_event(
                        webhook.ProcessContext(
                            owner=repo.owner.login,
                            repository=repo.name,
                            config=config,
                            application=application,
                            event_name="event",
                            event_data=event_data,
                            session=session,
                            github_application=github_application,
                            service_url=config["service-url"],
                        ),
                    )


def _get_dashboard_issue(
    github_application: configuration.GithubApplication,
    repo: github.Repository.Repository,
) -> github.Issue.Issue | None:
    open_issues = repo.get_issues(
        state="open",
        creator=github_application.integration.get_app().slug + "[bot]",  # type: ignore[arg-type]
    )
    if open_issues.totalCount > 0:
        for candidate in open_issues:
            if "dashboard" in candidate.title.lower().split():
                return candidate
    return None


def _process_dashboard_issue(
    config: dict[str, Any],
    session: sqlalchemy.orm.Session,
    event_data: dict[str, Any],
    application: str,
    owner: str,
    repository: str,
) -> None:
    """Process changes on the dashboard issue."""
    github_application = configuration.get_github_application(config, application)
    github_project = configuration.get_github_project(config, github_application, owner, repository)

    if event_data["issue"]["user"]["login"] == github_application.integration.get_app().slug + "[bot]":
        repo = github_project.repo
        dashboard_issue = _get_dashboard_issue(github_application, repo)

        if dashboard_issue and dashboard_issue.number == event_data["issue"]["number"]:
            _LOGGER.debug("Dashboard issue edited")
            old_data = event_data.get("changes", {}).get("body", {}).get("from", "")
            new_data = event_data["issue"]["body"]

            for name in config.get(f"application.{github_project.application.name}.modules", "").split():
                current_module = modules.MODULES.get(name)
                if current_module is None:
                    _LOGGER.error("Unknown module %s", name)
                    continue
                module_old = utils.get_dashboard_issue_module(old_data, name)
                module_new = utils.get_dashboard_issue_module(new_data, name)
                if module_old != module_new:
                    _LOGGER.debug("Dashboard issue edited for module %s: %s", name, current_module.title())
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
                            session.flush()
                            if action.checks:
                                webhook.create_checks(
                                    job,
                                    session,
                                    current_module,
                                    repo,
                                    {},
                                    config["service-url"],
                                    action.title,
                                )
                            session.commit()
    else:
        _LOGGER.debug(
            "Dashboard event ignored %s!=%s",
            event_data["issue"]["user"]["login"],
            github_application.integration.get_app().slug + "[bot]",
        )


# Where 2147483647 is the PostgreSQL max int, see: https://www.postgresql.org/docs/current/datatype-numeric.html
async def _get_process_one_job(
    config: dict[str, Any],
    Session: sqlalchemy.orm.sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
        sqlalchemy.orm.Session
    ],
    no_steal_long_pending: bool = False,
    make_pending: bool = False,
    max_priority: int = 2147483647,
) -> bool:
    _LOGGER.debug("Process one job (max priority: %i): Start", max_priority)
    with Session() as session:
        job = (
            session.query(models.Queue)
            .filter(
                models.Queue.status == models.JobStatus.NEW,
                models.Queue.priority <= max_priority,
            )
            .order_by(
                models.Queue.priority.asc(),
                models.Queue.created_at.asc(),
            )
            .with_for_update(of=models.Queue, skip_locked=True)
            .first()
        )
        if job is None:
            if no_steal_long_pending:
                _LOGGER.debug("Process one job (max priority: %i): No job to process", max_priority)
                return True
            # Very long pending job => error
            session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING,
                    models.Queue.created_at
                    < datetime.datetime.now(tz=datetime.UTC)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT_ERROR", "86400"))),
                )
                .values(status=models.JobStatus.ERROR),
            )
            # Get too old pending jobs
            session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING,
                    models.Queue.started_at
                    < datetime.datetime.now(tz=datetime.UTC)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT", "3600")) + 60),
                )
                .values(status=models.JobStatus.NEW),
            )
            session.commit()

            _LOGGER.debug("Process one job (max priority: %i): Steal long pending job", max_priority)
            return True

        await asyncio.create_task(
            _process_one_job(job, session, config, make_pending, max_priority),
            name=f"Process Job {job.id} - {job.event_name} - {job.module or '-'}",
        )

        return False


async def _process_one_job(
    job: models.Queue,
    session: sqlalchemy.orm.Session,
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
    message.title = f"Start process job '{job.event_name}' id: {job.id}, on {job.owner}/{job.repository} on module: {job.module}, on application {job.application}"
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
        job.status = models.JobStatus.PENDING
        job.started_at = datetime.datetime.now(tz=datetime.UTC)
        session.commit()
        _LOGGER.debug("Process one job (max priority: %i): Make pending", max_priority)
        return

    try:
        job.status = models.JobStatus.PENDING
        job.started_at = datetime.datetime.now(tz=datetime.UTC)
        session.commit()
        _NB_JOBS.labels(models.JobStatus.PENDING.name).set(
            session.query(models.Queue).filter(models.Queue.status == models.JobStatus.PENDING).count(),
        )

        success = True
        if not job.module:
            if job.event_data.get("type") == "event":
                _process_event(config, job.event_data, session)
                job.status = models.JobStatus.DONE
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            elif job.event_name == "dashboard":
                success = _validate_job(config, job.application, job.event_data)
                if success:
                    _LOGGER.info("Process dashboard issue %i", job.id)
                    _process_dashboard_issue(
                        config,
                        session,
                        job.event_data,
                        job.application,
                        job.owner,
                        job.repository,
                    )
                    job.status = models.JobStatus.DONE
                else:
                    job.status = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            else:
                _LOGGER.error("Unknown event name: %s", job.event_name)
                job.status = models.JobStatus.ERROR
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
                success = False
        else:
            success = _validate_job(config, job.application, job.event_data)
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
        if job.status == models.JobStatus.PENDING:
            _LOGGER.error("Job %s finished with pending status", job.id)
            job.status = models.JobStatus.ERROR
        job.finished_at = datetime.datetime.now(tz=datetime.UTC)
        session.commit()
        _RUNNING_JOBS.pop(job.id)

    _LOGGER.debug("Process one job (max priority: %i): Done", max_priority)


class _Run:
    def __init__(
        self,
        config: dict[str, Any],
        Session: sqlalchemy.orm.sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.orm.Session
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
            empty = True
            try:
                empty = await _get_process_one_job(
                    self.config,
                    self.Session,
                    no_steal_long_pending=self.end_when_empty,
                    max_priority=self.max_priority,
                )
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
    ) -> None:
        self.Session = Session  # pylint: disable=invalid-name
        self.last_run = time.time()

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        current_task = asyncio.current_task()
        if current_task is not None:
            current_task.set_name("PrometheusWatch")
        await asyncio.to_thread(self._watch)

    def _watch(self) -> None:
        cont = 0
        while True:
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
                        session.query(models.Queue).filter(models.Queue.status == status).count(),
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
            time.sleep(10)


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
                    models.Queue.status == models.JobStatus.PENDING,
                ),
            ):
                job.status = models.JobStatus.NEW
                job.finished_at = datetime.datetime.now(tz=datetime.UTC)
            session.commit()
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
    loop.slow_callback_duration = float(
        os.environ.get("GHCI_SLOW_CALLBACK_DURATION", "60"),
    )  # 1 minute by default

    def do_exit(loop: asyncio.AbstractEventLoop) -> None:
        print("Exiting...")
        loop.stop()

    for signal_type in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signal_type, functools.partial(do_exit, loop))

    loader = plaster.get_loader(args.config_uri)
    config = loader.get_settings("app:app")
    engine = sqlalchemy.engine_from_config(config, "sqlalchemy.")
    Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name

    # Create tables if they do not exist
    models.Base.metadata.create_all(engine)

    handle_sigint = HandleSigint(Session)
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, handle_sigint)

    if args.only_one:
        await _get_process_one_job(
            config,
            Session,
            no_steal_long_pending=args.exit_when_empty,
            make_pending=args.make_pending,
        )
        sys.exit(0)
    if args.make_pending:
        await _get_process_one_job(
            config,
            Session,
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
        tasks.append(asyncio.create_task(_PrometheusWatch(Session)(), name="Prometheus Watch"))

    tasks.extend(
        [
            asyncio.create_task(
                _Run(config, Session, args.exit_when_empty, priority)(),
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
