"""Webhook view."""

import logging
import os

import githubkit.webhooks
import pyramid.httpexceptions
import pyramid.request
import sqlalchemy.orm
from pyramid.view import view_config

from github_app_geo_project import models, module
from github_app_geo_project.views import get_event_loop

_LOGGER = logging.getLogger(__name__)
_APPLICATIONS_SLUG: dict[str, str] = {}


async def async_webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json

    github_secret = request.registry.settings.get(
        f"application.{application}.github_app_webhook_secret",
    )
    if github_secret:
        dry_run = os.environ.get("GHCI_WEBHOOK_SECRET_DRY_RUN", "false").lower() in (
            "true",
            "1",
            "yes",
            "on",
        )
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

    # triggering_actor can also be used to avoid infinite event loop
    if application not in _APPLICATIONS_SLUG:
        try:
            config = request.registry.settings
            private_key = "\n".join(
                [
                    e.strip()
                    for e in config[f"application.{application}.github_app_private_key"].strip().split("\n")
                ],
            )
            application_id = config[f"application.{application}.github_app_id"]

            aio_auth = githubkit.AppAuthStrategy(application_id, private_key)
            aio_github = githubkit.GitHub(aio_auth)
            aio_application_response = await aio_github.rest.apps.async_get_authenticated()
            aio_application = aio_application_response.parsed_data
            assert aio_application is not None
            if isinstance(aio_application.slug, str):
                _APPLICATIONS_SLUG[application] = aio_application.slug
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Event from the application itself, this can be source of infinite event loop",
            )

    if application in _APPLICATIONS_SLUG:  # noqa: SIM102
        if data.get("sender", {}).get("login") == _APPLICATIONS_SLUG[application] + "[bot]":
            _LOGGER.warning(
                "Event from the application itself, this can be source of infinite event loop",
            )

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
        if event_name == "issues":
            event = githubkit.webhooks.parse_obj("issues", data)
            if event.action == "edited" and event.issue and "dashboard" in event.issue.title:
                session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": module.PRIORITY_HIGH,
                            "application": application,
                            "owner": owner,
                            "repository": repository,
                            "github_event_name": "dashboard",
                            "github_event_data": data,
                            "module_event_name": "dashboard",
                            "module_event_data": {
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
        job.github_event_name = event_name
        job.github_event_data = data
        job.module = "dispatcher"
        job.module_event_name = event_name
        job.module_event_data = {
            "modules": request.registry.settings.get(
                f"application.{application}.modules",
                "",
            ).split(),
        }
        session.add(job)
        session.commit()
    return {}


@view_config(route_name="webhook", renderer="json")  # type: ignore[misc]
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    return get_event_loop().run_until_complete(async_webhook(request))
