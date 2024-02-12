"""Webhook view."""

import logging

import pyramid.request
import sqlalchemy  # pylint: disable=import-error
from pyramid.view import view_config

from github_app_geo_project import configuration, models
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json

    config = configuration.get_configuration(data["repository"]["full_name"])
    for name, module in modules.MODULES.items():
        module_config = cast(DD, config.get(name, {}))
        if (
            module_config.get("enabled", True)
            and module_config.get(
                "application", configuration.APPLICATION_CONFIGURATION["default-application"]
            )
            == application
        ):
            for action in module.get_actions(data):
                models.DBSession.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": action.priority,
                            "application": application,
                            "data": data,
                        }
                    )
                )
    return {}
