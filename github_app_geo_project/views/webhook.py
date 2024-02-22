"""Webhook view."""

import json
import logging
from typing import Any, Union, cast

import pyramid.request
import sqlalchemy.engine
import sqlalchemy.orm
from pyramid.view import view_config

from github_app_geo_project import application_configuration, configuration, models
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)

# curl -X POST http://localhost:9120/webhook/generic -d '{"repository":{"full_name": "sbrunner/test-github-app"}}'


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json
    _LOGGER.debug("Webhook received for %s, with:\n%s", application, json.dumps(data, indent=2))

    owner, repo = data["repository"]["full_name"].split("/")

    # TODO manage modification on dashboard issue

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.rw_engine
    with engine.connect() as session:
        process_event(request.registry.settings, session, application, data, owner, repo)
    return {}


def process_event(
    application_config: dict[str, Any],
    session: Union[sqlalchemy.orm.Session, sqlalchemy.engine.Connection],
    application: str,
    data: dict[str, Any],
    owner: str,
    repo: str,
) -> None:
    """Process the event."""
    config = configuration.get_configuration(application_config, owner, repo)

    for name in application_config.get(f"application.{application}.modules", "").split():
        module = modules.MODULES.get(name)
        if module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        module_config = cast(application_configuration.ModuleConfiguration, config.get(name, {}))
        if module_config.get("enabled", True):
            for action in module.get_actions(data):
                session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": action.priority,
                            "application": application,
                            "owner": owner,
                            "repository": repo,
                            "event_data": data,
                            "module": name,
                            "module_data": action.data,
                        }
                    )
                )
