"""Webhook view."""

import logging
from typing import Any, NamedTuple

import pyramid.request
import sqlalchemy.engine
import sqlalchemy.orm
from pyramid.view import view_config

from github_app_geo_project import configuration, models, module
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)

# curl -X POST http://localhost:9120/webhook/generic -d '{"repository":{"full_name": "sbrunner/test-github-app"}}'


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json

    _LOGGER.debug(
        "Webhook received for %s on%s", request.headers.get("X-GitHub-Event", "undefined"), application
    )

    application_object = None
    try:
        application_object = configuration.get_github_application(request.registry.settings, application)
        if data.get("sender", {}).get("login") == application_object.integration.get_app().slug + "[bot]":
            _LOGGER.info("Ignoring event from the application itself")
            return {}
    except Exception:  # pylint: disable=broad-except
        del configuration.GITHUB_APPLICATIONS[application]
        application_object = configuration.get_github_application(request.registry.settings, application)
        if data.get("sender", {}).get("login") == application_object.integration.get_app().slug + "[bot]":
            _LOGGER.info("Ignoring event from the application itself")
            return {}

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
    with engine.connect() as session:
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
                context.session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": action.priority,
                            "application": context.application,
                            "owner": context.owner,
                            "repository": context.repository,
                            "event_name": action.title or context.event_name,
                            "event_data": context.event_data,
                            "module": name,
                            "module_data": action.data,
                        }
                    )
                )
        except Exception as exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error while getting actions for %s: %s", name, exception)
