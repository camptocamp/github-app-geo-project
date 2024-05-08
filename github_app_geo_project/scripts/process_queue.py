"""
Process the jobs present in the database queue.
"""

import argparse
import asyncio
import contextvars
import datetime
import logging
import os
import socket
import subprocess  # nosec
import sys
import urllib.parse
from typing import Any, cast

import c2cwsgiutils.loader
import c2cwsgiutils.setup_process
import github
import plaster
import prometheus_client
import sentry_sdk
import sqlalchemy.orm
from c2cwsgiutils import prometheus
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
from prometheus_client.exposition import make_wsgi_app

from github_app_geo_project import configuration, models, module, project_configuration, utils
from github_app_geo_project.module import modules
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.views import output, webhook

_LOGGER = logging.getLogger(__name__)

_NB_JOBS = Gauge("ghci_jobs_number", "Number of jobs", ["status"])


class _Handler(logging.Handler):
    context_var: contextvars.ContextVar[int] = contextvars.ContextVar("job_id")

    def __init__(self, job_id: int) -> None:
        super().__init__()
        self.results: list[logging.LogRecord] = []
        self.job_id = job_id
        self.context_var.set(job_id)

    def emit(self, record: logging.LogRecord) -> None:
        if self.context_var.get() != self.job_id:
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
        attributes = f" style=\"{'; '.join(styles)}\"" if styles else ""
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
    if not github_app.id != installation_id:
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
            config, github_application, job.owner, job.repository
        )
        repo = github_project.repo

        if current_module.required_issue_dashboard():
            dashboard_issue = _get_dashboard_issue(github_application, repo)
            if dashboard_issue:
                issue_full_data = dashboard_issue.body
                issue_data = utils.get_dashboard_issue_module(issue_full_data, job.module)

        module_config = cast(
            project_configuration.ModuleConfiguration,
            configuration.get_configuration(config, job.owner, job.repository, job.application).get(
                job.module, {}
            ),
        )
        if job.check_run_id is not None:
            check_run = repo.get_check_run(job.check_run_id)
    if module_config.get("enabled", project_configuration.MODULE_ENABLED_DEFAULT):
        try:
            module_status = (
                session.query(models.ModuleStatus)
                .filter(models.ModuleStatus.module == job.module)
                .with_for_update(of=models.ModuleStatus)
                .one_or_none()
            )
            if module_status is None:
                module_status = models.ModuleStatus(module=job.module, data={})
                session.add(module_status)

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

            context = module.ProcessContext(
                session=session,
                github_project=github_project,  # type: ignore[arg-type]
                event_name=job.event_name,
                event_data=job.event_data,
                module_config=current_module.configuration_from_json(cast(dict[str, Any], module_config)),
                module_event_data=current_module.event_data_from_json(job.module_data),
                issue_data=issue_data,
                transversal_status=current_module.transversal_status_from_json(module_status.data or {}),
                job_id=job.id,
                service_url=config["service-url"],
            )
            root_logger.addHandler(handler)
            try:
                result = await current_module.process(context)
                if result and not result.success:
                    _LOGGER.warning("Module %s failed", job.module)
            finally:
                root_logger.removeHandler(handler)

            if github_project is not None:
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
            job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)

            job.log = "\n".join([handler.format(msg) for msg in handler.results])
            if result is not None and result.transversal_status is not None:
                module_status.data = current_module.transversal_status_to_json(result.transversal_status)
            if result is not None:
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
            session.commit()
            new_issue_data = result.dashboard if result is not None else None
        except github.GithubException as exception:
            job.status = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
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
        except subprocess.CalledProcessError as proc_error:
            job.status = models.JobStatus.ERROR
            job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
            message = module_utils.ansi_proc_message(proc_error)
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
            job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
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
                )
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
                dashboard_issue.body, job.module, current_module, new_issue_data
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
    config: dict[str, str], event_data: dict[str, str], session: sqlalchemy.orm.Session
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
                )
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
                        )
                    )


def _get_dashboard_issue(
    github_application: configuration.GithubApplication, repo: github.Repository.Repository
) -> github.Issue.Issue | None:
    open_issues = repo.get_issues(
        state="open", creator=github_application.integration.get_app().slug + "[bot]"  # type: ignore[arg-type]
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
                            )
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
                                    job, session, current_module, repo, {}, config["service-url"]
                                )
                            session.commit()
    else:
        _LOGGER.debug(
            "Dashboard event ignored %s!=%s",
            event_data["issue"]["user"]["login"],
            github_application.integration.get_app().slug + "[bot]",
        )


# Where 2147483647 is the PostgreSQL max int, see: https://www.postgresql.org/docs/current/datatype-numeric.html
async def _process_one_job(
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
                    < datetime.datetime.now(tz=datetime.timezone.utc)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT_ERROR", 86400))),
                )
                .values(status=models.JobStatus.ERROR)
            )
            # Get too old pending jobs
            session.execute(
                sqlalchemy.update(models.Queue)
                .where(
                    models.Queue.status == models.JobStatus.PENDING,
                    models.Queue.started_at
                    < datetime.datetime.now(tz=datetime.timezone.utc)
                    - datetime.timedelta(seconds=int(os.environ.get("GHCI_JOB_TIMEOUT", 3600)) + 60),
                )
                .values(status=models.JobStatus.NEW)
            )
            session.commit()

            _LOGGER.debug("Process one job (max priority: %i): Steal long pending job", max_priority)
            return True

        sentry_sdk.set_context("job", {"id": job.id, "event": job.event_name, "module": job.module or "-"})

        # Capture_logs
        root_logger = logging.getLogger()
        handler = _Handler(job.id)
        handler.setFormatter(_Formatter("%(levelname)-5.5s %(pathname)s:%(lineno)d %(funcName)s()"))

        module_data_formatted = utils.format_json(job.module_data)
        event_data_formatted = utils.format_json(job.event_data)
        message = module_utils.HtmlMessage(
            f"<p>module data:</p>{module_data_formatted}<p>event data:</p>{event_data_formatted}"
        )
        message.title = f"Start process job '{job.event_name}' id: {job.id}, on {job.owner}/{job.repository} on module: {job.module}, on application {job.application}"
        root_logger.addHandler(handler)
        _LOGGER.info(message)
        root_logger.removeHandler(handler)

        if make_pending:
            _LOGGER.info("Make job ID %s pending", job.id)
            job.status = models.JobStatus.PENDING
            job.started_at = datetime.datetime.now(tz=datetime.timezone.utc)
            session.commit()
            _LOGGER.debug("Process one job (max priority: %i): Make pending", max_priority)
            return False

        try:
            job.status = models.JobStatus.PENDING
            job.started_at = datetime.datetime.now(tz=datetime.timezone.utc)
            session.commit()
            _NB_JOBS.labels(models.JobStatus.PENDING).set(
                session.query(models.Queue).filter(models.Queue.status == models.JobStatus.PENDING).count()
            )

            success = True
            if not job.module:
                if job.event_data.get("type") == "event":
                    _process_event(config, job.event_data, session)
                    job.status = models.JobStatus.DONE
                    job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
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
                    job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
                else:
                    _LOGGER.error("Unknown event name: %s", job.event_name)
                    job.status = models.JobStatus.ERROR
                    job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
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
            assert job.status != models.JobStatus.PENDING
            job.finished_at = datetime.datetime.now(tz=datetime.timezone.utc)
            session.commit()

        _LOGGER.debug("Process one job (max priority: %i): Done", max_priority)
        return False


class _Run:
    def __init__(
        self,
        config: dict[str, Any],
        Session: sqlalchemy.orm.sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.orm.Session
        ],
        return_when_empty: bool,
        max_priority: int,
    ):
        self.config = config
        self.Session = Session  # pylint: disable=invalid-name
        self.end_when_empty = return_when_empty
        self.max_priority = max_priority

    async def __call__(self, *args: Any, **kwds: Any) -> Any:
        job_timeout = int(os.environ.get("GHCI_JOB_TIMEOUT", 3600))
        empty_thread_sleep = int(os.environ.get("GHCI_EMPTY_THREAD_SLEEP", 10))

        while True:
            empty = True
            try:
                async with asyncio.timeout(job_timeout):
                    empty = await _process_one_job(
                        self.config,
                        self.Session,
                        no_steal_long_pending=self.end_when_empty,
                        max_priority=self.max_priority,
                    )
                    if self.end_when_empty and empty:
                        return
            except asyncio.TimeoutError:
                _LOGGER.exception("Timeout")
            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to process job")

            await asyncio.sleep(empty_thread_sleep if empty else 0)


class _UpdateCounter:
    def __init__(
        self,
        Session: sqlalchemy.orm.sessionmaker[  # pylint: disable=invalid-name,unsubscriptable-object
            sqlalchemy.orm.Session
        ],
    ):
        self.Session = Session  # pylint: disable=invalid-name

    async def __call__(self, *args: Any, **kwds: Any) -> Any:
        while True:
            with self.Session() as session:
                for status in models.JobStatus:
                    _NB_JOBS.labels(status.name).set(
                        session.query(models.Queue).filter(models.Queue.status == status).count()
                    )
                    await asyncio.sleep(0)
            await asyncio.sleep(10)


async def _async_main() -> None:
    """Process the jobs present in the database queue."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exit-when-empty", action="store_true", help="Exit when the queue is empty")
    parser.add_argument("--only-one", action="store_true", help="Exit after processing one job")
    parser.add_argument("--make-pending", action="store_true", help="Make one job in pending")
    c2cwsgiutils.setup_process.fill_arguments(parser)
    args = parser.parse_args()
    c2cwsgiutils.setup_process.init(args.config_uri)
    loader = plaster.get_loader(args.config_uri)
    config = loader.get_settings("app:app")
    engine = sqlalchemy.engine_from_config(config, "sqlalchemy.")
    Session = sqlalchemy.orm.sessionmaker(bind=engine)  # pylint: disable=invalid-name
    # Create tables if they do not exist
    models.Base.metadata.create_all(engine)
    if args.only_one:
        await _process_one_job(
            config, Session, no_steal_long_pending=args.exit_when_empty, make_pending=args.make_pending
        )
        sys.exit(0)
    if args.make_pending:
        await _process_one_job(config, Session, no_steal_long_pending=args.exit_when_empty, make_pending=True)
        sys.exit(0)

    if not args.exit_when_empty and "C2C_PROMETHEUS_PORT" in os.environ:
        prometheus_client.start_http_server(int(os.environ["C2C_PROMETHEUS_PORT"]))

    priority_groups = [int(e) for e in os.environ.get("GHCI_PRIORITY_GROUPS", "2147483647").split(",")]

    threads_call = []
    if not args.exit_when_empty:
        threads_call.append(_UpdateCounter(Session)())

    for priority in priority_groups:
        threads_call.append(_Run(config, Session, args.exit_when_empty, priority)())
    await asyncio.gather(*threads_call)


def main() -> None:
    """Process the jobs present in the database queue."""
    socket.setdefaulttimeout(int(os.environ.get("GHCI_SOCKET_TIMEOUT", 120)))
    with asyncio.Runner() as runner:
        runner.run(_async_main())


if __name__ == "__main__":
    main()
