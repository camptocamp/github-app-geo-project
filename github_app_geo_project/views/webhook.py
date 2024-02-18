"""Webhook view."""

import logging
from typing import cast

import pyramid.request
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import configuration, models, project_configuration
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)

# curl -X POST http://localhost:9120/webhook/generic -d '{"repository":{"full_name": "sbrunner/test-github-app"}}'


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json
    print(data)
    owner, repo = data["repository"]["full_name"].split("/")
    config = configuration.get_configuration(request.registry.settings, owner, repo)

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.rw_engine
    with engine.connect() as session:
        for name, module in modules.MODULES.items():
            module_config = cast(project_configuration.ModuleConfiguration, config.get(name, {}))
            if (
                module_config.get("enabled", True)
                and module_config.get("application", request.matchdict["application"]) == application
            ):
                for action in module.get_actions(data):
                    session.execute(
                        sqlalchemy.insert(models.Queue).values(
                            {
                                "priority": action.priority,
                                "application": application,
                                "data": data,
                            }
                        )
                    )
    return {}
