"""
Process the jobs present in the database queue.
"""

import argparse
import datetime
import json
import logging
import os
import time
from typing import Any, cast

import c2cwsgiutils.loader
import c2cwsgiutils.setup_process
import github
import plaster
import pygments.formatters
import pygments.lexers
import sqlalchemy.orm

from github_app_geo_project import configuration, models, module, project_configuration, utils
from github_app_geo_project.module import modules
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.views import webhook

_LOGGER = logging.getLogger(__name__)


class _Handler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
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


def _process_job(
    config: dict[str, str],
    session: sqlalchemy.orm.Session,
    event_data: dict[str, Any],
    module_data: dict[str, Any],
    owner: str,
    repository: str,
    job_id: int,
    job_priority: int,
    module_name: str,
    application: str,
    event_name: str,
    root_logger: logging.Logger,
    handler: _Handler,
) -> bool:
    current_module = modules.MODULES.get(module_name)
    if current_module is None:
        _LOGGER.error("Unknown module %s", module_name)
        return False

    new_issue_data = None
    issue_data = ""
    module_config: project_configuration.ModuleConfiguration = {}
    github_project = cast(configuration.GithubProject, None)
    if "TEST_APPLICATION" not in os.environ:
        github_application = configuration.get_github_application(config, application)
        github_project = configuration.get_github_project(config, github_application, owner, repository)

        if current_module.required_issue_dashboard():
            repository_full = f"{owner}/{repository}"
            repo = github_project.github.get_repo(repository_full)
            dashboard_issue = _get_dashboard_issue(github_application, repo)
            if dashboard_issue:
                issue_full_data = dashboard_issue.body
                issue_data = utils.get_dashboard_issue_module(issue_full_data, module_name)

        module_config = cast(
            project_configuration.ModuleConfiguration,
            configuration.get_configuration(config, owner, repository, application).get(module_name, {}),
        )
    if module_config.get("enabled", project_configuration.MODULE_ENABLED_DEFAULT):
        module_status = (
            session.query(models.ModuleStatus)
            .filter(models.ModuleStatus.module == module_name)
            .with_for_update(of=models.ModuleStatus)
            .one_or_none()
        )
        if module_status is None:
            module_status = models.ModuleStatus(module=module_name, data={})
            session.add(module_status)
        try:
            root_logger.addHandler(handler)

            result = current_module.process(
                module.ProcessContext(
                    session=session,
                    github_project=github_project,
                    event_name=event_name,
                    event_data=event_data,
                    module_config=module_config,
                    module_data=module_data,
                    issue_data=issue_data,
                    transversal_status=module_status.data or {},
                    job_id=job_id,
                    service_url=config["service-url"],
                )
            )

            root_logger.removeHandler(handler)

            session.execute(
                sqlalchemy.update(models.Queue)
                .where(models.Queue.id == job_id)
                .values(log="\n".join([handler.format(msg) for msg in handler.results]))
            )
            session.commit()
            if result is not None and result.transversal_status is not None:
                module_status.data = result.transversal_status
                session.commit()
            if result is not None:
                for action in result.actions:
                    session.execute(
                        sqlalchemy.insert(models.Queue).values(
                            {
                                "priority": action.priority if action.priority >= 0 else job_priority,
                                "application": application,
                                "owner": owner,
                                "repository": repository,
                                "event_name": action.title or event_name,
                                "event_data": event_data,
                                "module": module_name,
                                "module_data": action.data,
                            }
                        )
                    )
            new_issue_data = result.dashboard if result is not None else None
        except github.GithubException as exception:
            _LOGGER.exception(
                "Failed to process job id: %s on module: %s, return data:\n%s\nreturn headers:\n%s\nreturn message:\n%s\nreturn status: %s",
                job_id,
                module_name,
                exception.data,
                ("\n".join(f"{k}: {v}" for k, v in exception.headers.items()) if exception.headers else ""),
                exception.message,
                exception.status,
            )
            raise
        except Exception:
            _LOGGER.exception("Failed to process job id: %s on module: %s", job_id, module_name)
            root_logger.removeHandler(handler)
            raise
    else:
        _LOGGER.info("Module %s is disabled", module_name)
        try:
            current_module.cleanup(
                module.CleanupContext(
                    github_project=github_project,
                    event_name="event",
                    event_data=event_data,
                    module_data=module_data,
                )
            )
        except Exception:
            _LOGGER.exception(
                "Failed to cleanup job id: %s on module: %s, module data:\n%s\nevent data:\n%s",
                job_id,
                module_name,
                module_data,
                event_data,
            )
            raise

    if current_module.required_issue_dashboard() and new_issue_data is not None:
        dashboard_issue = _get_dashboard_issue(github_application, repo)

        if dashboard_issue:
            issue_full_data = utils.update_dashboard_issue_module(
                dashboard_issue.body, module_name, current_module, new_issue_data
            )
            _LOGGER.debug("Update issue %s, with:\n%s", dashboard_issue.number, issue_full_data)
            dashboard_issue.edit(body=issue_full_data)
        elif new_issue_data:
            issue_full_data = utils.update_dashboard_issue_module(
                f"This issue is the dashboard used by GHCI modules.\n\n[Project on GHCI]({config['service-url']}project/{owner}/{repository})\n\n",
                module_name,
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
        repository_full = f"{owner}/{repository}"
        repo = github_project.github.get_repo(repository_full)
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
                            )
                        ):
                            session.execute(
                                sqlalchemy.insert(models.Queue).values(
                                    {
                                        "priority": (
                                            action.priority
                                            if action.priority >= 0
                                            else module.PRIORITY_DASHBOARD
                                        ),
                                        "application": github_project.application.name,
                                        "owner": github_project.owner,
                                        "repository": github_project.repository,
                                        "event_name": action.title or "dashboard",
                                        "event_data": {
                                            "type": "dashboard",
                                            "old_data": module_old,
                                            "new_data": module_new,
                                        },
                                        "module": name,
                                        "module_data": action.data,
                                    }
                                )
                            )
    else:
        _LOGGER.debug(
            "Dashboard event ignored %s!=%s",
            event_data["issue"]["user"]["login"],
            github_application.integration.get_app().slug + "[bot]",
        )


def main() -> None:
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

    while True:
        with Session() as session:
            job = (
                session.query(models.Queue)
                .filter(
                    models.Queue.status == models.JobStatus.NEW,
                )
                .order_by(
                    models.Queue.priority.desc(),
                    models.Queue.created_at.asc(),
                )
                .with_for_update(of=models.Queue, skip_locked=True)
                .first()
            )
            if job is None:
                if args.exit_when_empty:
                    break
                # Get too old pending jobs
                session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(
                        models.Queue.status == models.JobStatus.PENDING,
                        models.Queue.started_at
                        < datetime.datetime.now(tz=datetime.timezone.utc)
                        - datetime.timedelta(seconds=int(os.environ.get("JOB_TIMEOUT", 3600))),
                    )
                    .values(status=models.JobStatus.NEW)
                )
                session.commit()

                time.sleep(1)
                continue

            job.status = models.JobStatus.PENDING
            job.started_at = datetime.datetime.now(tz=datetime.timezone.utc)
            session.commit()
            if args.make_pending:
                _LOGGER.info("Make job %s pending", args.make_pending)
                break

            # Force get job
            job_id = job.id

            # Capture_logs
            root_logger = logging.getLogger()
            handler = _Handler()
            handler.setFormatter(_Formatter("%(levelname)-5.5s %(pathname)s:%(lineno)d %(funcName)s()"))
            root_logger.addHandler(handler)

            lexer = pygments.lexers.JsonLexer()
            formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")

            module_data_formated = pygments.highlight(json.dumps(job.module_data, indent=4), lexer, formatter)
            event_data_formated = pygments.highlight(json.dumps(job.event_data, indent=4), lexer, formatter)
            message = module_utils.HtmlMessage(
                f"<p>module data:</p>{module_data_formated}<p>event data:</p>{event_data_formated}"
            )
            message.title = f"Start process job '{job.event_name}' id: {job_id}, on {job.owner}/{job.repository} on module: {job.module}, on application {job.application}"
            _LOGGER.info(message.to_html(style="collapse"))

            root_logger.removeHandler(handler)

            job_id = job.id
            job_priority = job.priority
            job_application = job.application
            job_module = job.module
            owner = job.owner
            repository = job.repository
            event_data = job.event_data
            event_name = job.event_name
            module_data = job.module_data

            try:
                success = True
                if not job.module:
                    if event_data.get("type") == "event":
                        _process_event(config, event_data, session)
                    elif event_name == "dashboard":
                        success = _validate_job(config, job_application, event_data)
                        if success:
                            _LOGGER.info("Process dashboard issue %i", job_id)
                            _process_dashboard_issue(
                                config,
                                session,
                                event_data,
                                job_application,
                                owner,
                                repository,
                            )
                    else:
                        _LOGGER.error("Unknown event name: %s", event_name)
                        success = False
                else:
                    success = _validate_job(config, job_application, event_data)
                    if success:
                        success = _process_job(
                            config,
                            session,
                            event_data,
                            module_data,
                            owner,
                            repository,
                            job_id,
                            job_priority,
                            job_module,
                            job_application,
                            job.event_name,
                            root_logger,
                            handler,
                        )

                session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(models.Queue.id == job_id)
                    .values(
                        status=models.JobStatus.DONE if success else models.JobStatus.ERROR,
                        finished_at=datetime.datetime.now(tz=datetime.timezone.utc),
                    )
                )
                session.commit()

            except Exception:  # pylint: disable=broad-exception-caught
                _LOGGER.exception("Failed to process job id: %s on module: %s.", job_id, job_module or "-")
                root_logger.removeHandler(handler)
                with Session() as session:
                    session.execute(
                        sqlalchemy.update(models.Queue)
                        .where(models.Queue.id == job_id)
                        .values(
                            status=models.JobStatus.ERROR,
                            finished_at=datetime.datetime.now(tz=datetime.timezone.utc),
                            log="\n".join([handler.format(msg) for msg in handler.results]),
                        )
                    )
                    session.commit()
            finally:
                root_logger.removeHandler(handler)
        if args.only_one:
            break


if __name__ == "__main__":
    main()
