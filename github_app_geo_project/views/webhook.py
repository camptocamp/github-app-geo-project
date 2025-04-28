"""Webhook view."""

import logging
import os
from typing import Any

import githubkit.exception
import githubkit.webhooks
import pyramid.httpexceptions
import pyramid.request
import sqlalchemy.orm
from pyramid.view import view_config

from github_app_geo_project import configuration, models, module
from github_app_geo_project.views import get_event_loop

_LOGGER = logging.getLogger(__name__)


def _get_re_requested_check_suite_id(event_name: str, event_data: dict[str, Any]) -> int | None:
    """Check if the event is a rerequested event."""
    if event_name != "check_run":
        return None

    event_data_check_suite = githubkit.webhooks.parse_obj("check_suite", event_data)
    if (
        event_data_check_suite.action == "rerequested"
        and event_data_check_suite.check_suite
        and event_data_check_suite.check_suite.id
        and "TEST_APPLICATION" not in os.environ
    ):
        return event_data_check_suite.check_suite.id
    return None


async def async_webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json

    github_secret = request.registry.settings.get(
        f"application.{application}.github_app_webhook_secret",
    )
    if github_secret:
        dry_run = os.environ.get("GHCI_WEBHOOK_SECRET_DRY_RUN", "false").lower() in ("true", "1", "yes", "on")
        if "X-Hub-Signature-256" not in request.headers:
            _LOGGER.error("No signature in the request")
            if not dry_run:
                message = "No signature in the request"
                raise pyramid.httpexceptions.HTTPBadRequest(message)

        elif not githubkit.webhooks.verify(
            github_secret,
            request.body,
            request.headers["X-Hub-Signature-256"],
        ):
            _LOGGER.error("Invalid signature in the request")
            if not dry_run:
                message = "Invalid signature in the request"
                raise pyramid.httpexceptions.HTTPBadRequest(message)

    event_name = request.headers.get("X-GitHub-Event", "undefined")
    _LOGGER.debug(
        "Webhook received for %s on %s",
        event_name,
        application,
    )

    application_object = None
    # triggering_actor can also be used to avoid infinite event loop
    try:
        application_object = await configuration.get_github_application(
            request.registry.settings,
            application,
        )
        if data.get("sender", {}).get("login") == application_object.slug + "[bot]":
            _LOGGER.warning("Event from the application itself, this can be source of infinite event loop")
    except Exception:  # pylint: disable=broad-exception-caught
        application_object = await configuration.get_github_application(
            request.registry.settings,
            application,
        )
        if data.get("sender", {}).get("login") == application_object.slug + "[bot]":
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
    project_github = (
        await configuration.get_github_project(
            request.registry.settings,
            application,
            owner,
            repository,
        )
        if "TEST_APPLICATION" not in os.environ
        else None
    )
    with SessionMaker() as session:
        re_requested_check_suite_id = _get_re_requested_check_suite_id(
            event_name,
            data,
        )
        if re_requested_check_suite_id is not None and "TEST_APPLICATION" not in os.environ:
            assert project_github is not None
            try:
                check_suite = await project_github.aio_github.rest.checks.async_get_suite(
                    owner=owner,
                    repo=repository,
                    check_suite_id=re_requested_check_suite_id,
                )
                check_runs = await project_github.aio_github.rest.checks.async_list_for_suite(
                    owner=owner,
                    repo=repository,
                    check_suite_id=re_requested_check_suite_id,
                )
                for check_run in check_runs.parsed_data.check_runs:
                    _LOGGER.info(
                        "Re request the check run %s from check suite %s",
                        check_run.id,
                        check_suite.parsed_data.id,
                    )
                    session.execute(
                        sqlalchemy.update(models.Queue)
                        .where(models.Queue.check_run_id == check_run.id)
                        .values(
                            {
                                "status": models.JobStatus.NEW,
                                "started_at": None,
                                "finished_at": None,
                            },
                        ),
                    )
                    session.commit()
                    await project_github.aio_github.rest.checks.async_update(
                        owner=owner,
                        repo=repository,
                        check_run_id=check_run.id,
                        data={"status": "queued"},
                    )
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code == 404:
                    _LOGGER.error("Repository not found: %s/%s", owner, repository)  # noqa: TRY400
                else:
                    _LOGGER.exception("Error while getting check suite")

        elif event_name == "issues":
            event = githubkit.webhooks.parse_obj("issues", data)
            if event.action == "edited" and event.issue and "dashboard" in event.issue.title:
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
                        },
                    ),
                )

        _LOGGER.debug("Processing event for application %s", application)
        job = models.Queue()
        job.priority = 0
        job.application = application
        job.owner = owner
        job.repository = repository
        job.event_name = event_name
        job.event_data = data
        job.module = "webhook"
        job.module_data = {
            "modules": request.registry.settings.get(f"application.{application}.modules", "").split(),
        }
        session.add(job)
        session.commit()
    return {}


@view_config(route_name="webhook", renderer="json")  # type: ignore[misc]
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    return get_event_loop().run_until_complete(async_webhook(request))
