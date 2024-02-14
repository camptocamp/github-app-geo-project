"""Output view."""

import logging
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import configuration, models
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="project", renderer="github_app_geo_project:templates/output.html")  # type: ignore
def project(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the output of a job."""
    select = sqlalchemy.select(models.Output).where(
        models.Output.repository == request.matchdict["repository"]
    )
    if "only_error" in request.params:
        select = select.where(models.Output.status == models.STATUS_ERROR)

    out = models.DBSession.execute(select).partitions(20)

    config = configuration.get_configuration(request.registry.config, request.matchdict["repository"])

    module_config = []
    for module_name, module in modules.MODULES.items():
        module_config.append(
            {
                "name": module_name,
                "title": module.title(),
                "description": module.description(),
                "configuration_url": module.documentation_url(),
                "configuration": config.get(module_name, {}),
            }
        )

    return {
        "repository": request.matchdict["repository"],
        "output": out,
        "issue_url": "...",
        "module_configuration": module_config,
    }
