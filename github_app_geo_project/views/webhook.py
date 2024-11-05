"""Webhook view."""

import hashlib
import hmac
import logging
import os
import urllib.parse
from typing import Any, NamedTuple

import github
import pyramid.request
import sqlalchemy.engine
import sqlalchemy.orm
from numpy import where
from pyramid.view import view_config
from sqlalchemy.orm import Session

from github_app_geo_project import configuration, models, module
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)

# curl -X POST http://localhost:9120/webhook/generic -d '{"repository":{"full_name": "sbrunner/test-github-app"}}'


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json

    github_secret = request.registry.settings.get(f"application.{application}.github_app_webhook_secret")
    if github_secret:
        dry_run = os.environ.get("GHCI_WEBHOOK_SECRET_DRY_RUN", "false").lower() in ("true", "1", "yes", "on")
        if "X-Hub-Signature-256" not in request.headers:
            _LOGGER.error("No signature in the request")
            if not dry_run:
                raise pyramid.httpexceptions.HTTPBadRequest("No signature in the request")

        else:
            our_signature = hmac.new(
                key=github_secret.encode("utf-8"),
                msg=request.body,
                digestmod=hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(
                our_signature, request.headers["X-Hub-Signature-256"].split("=", 1)[-1]
            ):
                _LOGGER.error("Invalid signature in the request")
                if not dry_run:
                    raise pyramid.httpexceptions.HTTPBadRequest("Invalid signature in the request")

    _LOGGER.debug(
        "Webhook received for %s on %s", request.headers.get("X-GitHub-Event", "undefined"), application
    )

    application_object = None
    # triggering_actor can also be used to avoid infinite event loop
    try:
        application_object = configuration.get_github_application(request.registry.settings, application)
        if data.get("sender", {}).get("login") == application_object.integration.get_app().slug + "[bot]":
            _LOGGER.warning("Event from the application itself, this can be source of infinite event loop")
    except Exception:  # pylint: disable=broad-except
        del configuration.GITHUB_APPLICATIONS[application]
        application_object = configuration.get_github_application(request.registry.settings, application)
        if data.get("sender", {}).get("login") == application_object.integration.get_app().slug + "[bot]":
            _LOGGER.warning("Event from the application itself, this can be source of infinite event loop")

    if "account" in data.get("installation", {}):
        if "repositories" in data:
            _LOGGER.info(
                "Installation event on '%s' with repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["name"] for repo in data["repositories"]]),
            )
        if data.get("repositories_added", []):
            _LOGGER.info(
                "Installation event on '%s' with added repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["full_name"] for repo in data["repositories_added"]]),
            )
        if data.get("repositories_removed", []):
            _LOGGER.info(
                "Installation event on '%s' with removed repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["full_name"] for repo in data["repositories_removed"]]),
            )
        return {}
    owner, repository = data["repository"]["full_name"].split("/")

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.rw_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa
    with SessionMaker() as session:
        if data.get("action") == "rerequested" and data.get("check_suite", {}).get("id"):
            if "TEST_APPLICATION" not in os.environ:
                try:
                    project_github = configuration.get_github_project(
                        request.registry.settings, application, owner, repository
                    )
                    check_suite = project_github.repo.get_check_suite(data["check_suite"]["id"])
                    for check_run in check_suite.get_check_runs():
                        _LOGGER.info(
                            "Rerequest the check run %s from check suite %s", check_run.id, check_suite.id
                        )
                        session.execute(
                            sqlalchemy.update(models.Queue)
                            .where(models.Queue.check_run_id == check_run.id)
                            .values(
                                {
                                    "status": models.JobStatus.NEW,
                                    "started_at": None,
                                    "finished_at": None,
                                }
                            )
                        )
                        session.commit()
                        check_run.edit(status="queued")
                except github.GithubException as exception:
                    if exception.status == 404:
                        _LOGGER.error("Repository not found: %s/%s", owner, repository)
                    else:
                        _LOGGER.exception("Error while getting check suite")

        if data.get("action") == "rerequested" and data.get("check_run", {}).get("id"):
            _LOGGER.info("Rerequest the check run %s", data["check_run"]["id"])
            session.execute(
                sqlalchemy.update(models.Queue)
                .where(models.Queue.check_run_id == data["check_run"]["id"])
                .values(
                    {
                        "status": models.JobStatus.NEW,
                        "started_at": None,
                        "finished_at": None,
                    }
                )
            )
            if "TEST_APPLICATION" not in os.environ:
                try:
                    project_github = configuration.get_github_project(
                        request.registry.settings, application, owner, repository
                    )
                    project_github.repo.get_check_run(data["check_run"]["id"]).edit(status="queued")
                except github.GithubException as exception:
                    if exception.status == 404:
                        _LOGGER.error("Repository not found: %s/%s", owner, repository)
                    else:
                        _LOGGER.exception("Error while getting check run")

        if data.get("action") == "edited" and "issue" in data:
            if "dashboard" in data["issue"]["title"].lower().split():
                session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": module.PRIORITY_HIGH,
                            "application": application,
                            "owner": owner,
                            "repository": repository,
                            "event_name": "dashboard",
                            "event_data": data,
                            "module_data": {
                                "type": "dashboard",
                            },
                        }
                    )
                )

        process_event(
            ProcessContext(
                owner=owner,
                repository=repository,
                config=request.registry.settings,
                application=application,
                event_name=request.headers.get("X-GitHub-Event", "undefined"),
                event_data=data,
                session=session,
                github_application=application_object,
                service_url=request.route_url("home"),
            )
        )
        session.commit()
    return {}


class ProcessContext(NamedTuple):
    """The context of the process."""

    owner: str
    """The GitHub project owner"""
    repository: str
    """The GitHub project repository"""
    config: dict[str, Any]
    """The application configuration"""
    application: str
    """The application name"""
    event_name: str
    """The event name present in the X-GitHub-Event header"""
    event_data: dict[str, Any]
    """The event data"""
    session: sqlalchemy.orm.Session
    """The session to be used"""
    github_application: configuration.GithubApplication
    """The github application."""
    service_url: str
    """The service URL"""


def process_event(context: ProcessContext) -> None:
    """Process the event."""
    _LOGGER.debug("Processing event for application %s", context.application)
    for name in context.config.get(f"application.{context.application}.modules", "").split():
        current_module = modules.MODULES.get(name)
        if current_module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        _LOGGER.info(
            "Getting actions for the application: %s, repository: %s/%s, module: %s",
            context.application,
            context.owner,
            context.repository,
            name,
        )
        try:
            for action in current_module.get_actions(
                module.GetActionContext(
                    event_name=context.event_name,
                    event_data=context.event_data,
                    owner=context.owner,
                    repository=context.repository,
                    github_application=context.github_application,
                )
            ):
                priority = action.priority if action.priority >= 0 else module.PRIORITY_STANDARD
                event_name = action.title or context.event_name
                module_data = current_module.event_data_to_json(action.data)

                jobs_unique_on = current_module.jobs_unique_on()
                if jobs_unique_on:

                    update = (
                        sqlalchemy.update(models.Queue)
                        .where(models.Queue.status == models.JobStatus.NEW)
                        .where(models.Queue.application == context.application)
                        .where(models.Queue.module == name)
                    )
                    for key in jobs_unique_on:
                        if key == "priority":
                            update = update.where(models.Queue.priority == priority)
                        elif key == "owner":
                            update = update.where(models.Queue.owner == context.owner)
                        elif key == "repository":
                            update = update.where(models.Queue.repository == context.repository)
                        elif key == "event_name":
                            update = update.where(models.Queue.event_name == event_name)
                        elif key == "event_data":
                            update = update.where(models.Queue.event_data == context.event_data)
                        elif key == "module_data":
                            update = update.where(models.Queue.module_data == module_data)
                        else:
                            _LOGGER.error("Unknown jobs_unique_on key: %s", key)

                    update = update.values(
                        {
                            "status": models.JobStatus.SKIPPED,
                        }
                    )

                    context.session.execute(update)

                job = models.Queue()
                job.priority = priority
                job.application = context.application
                job.owner = context.owner
                job.repository = context.repository
                job.event_name = event_name
                job.event_data = context.event_data
                job.module = name
                job.module_data = module_data
                context.session.add(job)
                context.session.flush()

                repo = None
                if "TEST_APPLICATION" not in os.environ:
                    github_project = configuration.get_github_project(
                        context.config, context.application, context.owner, context.repository
                    )
                    repo = github_project.repo

                should_create_checks = action.checks
                if should_create_checks is None:
                    # Auto (major of event that comes from GitHub)
                    for event_name in ["pull_request", "pusher", "check_run", "check_suite", "workflow_run"]:
                        if event_name in context.event_data:
                            should_create_checks = True
                            break
                if should_create_checks:
                    create_checks(
                        job,
                        context.session,
                        current_module,
                        repo,  # type: ignore[arg-type]
                        context.event_data,
                        context.service_url,
                        action.title,
                    )

                context.session.commit()
        except Exception as exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error while getting actions for %s: %s", name, exception)


def create_checks(
    job: models.Queue,
    session: Session,
    current_module: module.Module[Any, Any, Any],
    repo: github.Repository.Repository,
    event_data: dict[str, Any],
    service_url: str,
    sub_name: str | None = None,
) -> github.CheckRun.CheckRun:
    """Create the GitHub check run."""
    # Get the job id from the database
    session.flush()

    service_url = service_url if service_url.endswith("/") else service_url + "/"
    service_url = urllib.parse.urljoin(service_url, "logs/")
    service_url = urllib.parse.urljoin(service_url, str(job.id))

    sha = None
    if event_data.get("pull_request", {}).get("head", {}).get("sha"):
        sha = event_data["pull_request"]["head"]["sha"]
    if "ref" in event_data and "after" in event_data and event_data.get("deleted") is False:
        sha = event_data["after"]
    if event_data.get("workflow_run", {}).get("head_sha"):
        sha = event_data["workflow_run"]["head_sha"]
    if event_data.get("check_suite", {}).get("head_sha"):
        sha = event_data["check_suite"]["head_sha"]
    if event_data.get("check_run", {}).get("head_sha"):
        sha = event_data["check_run"]["head_sha"]
    if sha is None:
        sha = repo.get_branch(repo.default_branch).commit.sha

    name = f"{current_module.title()}: {sub_name}" if sub_name else current_module.title()
    check_run = repo.create_check_run(
        name=name,
        head_sha=sha,
        details_url=service_url,
        external_id=str(job.id),
    )
    job.check_run_id = check_run.id
    session.commit()
    return check_run
