"""Output view."""

import logging
from typing import Any

import pygments.formatters
import pygments.lexers
import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy
import yaml
from pyramid.view import view_config

from github_app_geo_project import configuration, models
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="project", renderer="github_app_geo_project:templates/project.html")  # type: ignore
def project(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the output of a job."""
    repository = f'{request.matchdict["owner"]}/{request.matchdict["repository"]}'
    permission = request.has_permission(
        repository,
        {"github_repository": repository, "github_access_type": "admin"},
    )
    has_access = isinstance(permission, pyramid.security.Allowed)
    if not has_access:
        return {
            "styles": "",
            "repository": repository,
            "output": [],
            "error": "Access Denied",
            "issue_url": "",
            "module_configuration": [],
        }
    try:
        config = configuration.get_configuration(
            request.registry.settings, request.matchdict["owner"], request.matchdict["repository"]
        )
    except Exception:  # pylint: disable=broad-exception-caught
        _LOGGER.exception("Cannot get the configuration: %s")
        return {
            "styles": "",
            "repository": repository,
            "output": [],
            "error": "You need to install the main GitHub App, see logs for details",
            "issue_url": "",
            "module_configuration": [],
        }
    lexer = pygments.lexers.YamlLexer()
    formatter = pygments.formatters.HtmlFormatter()

    select = sqlalchemy.select(models.Output).where(
        models.Output.repository == request.matchdict["repository"]
    )
    if "only_error" in request.params:
        select = select.where(models.Output.status == models.OutputStatus.ERROR)

    module_names = set()
    for app in request.registry.settings["applications"].split():
        module_names.update(request.registry.settings[f"application.{app}.modules"].split())
    module_config = []
    for module_name in module_names:
        if module_name not in modules.MODULES:
            _LOGGER.error("Unknown module %s", module_name)
            continue
        module = modules.MODULES[module_name]
        module_config.append(
            {
                "name": module_name,
                "title": module.title(),
                "description": module.description(),
                "documentation_url": module.documentation_url(),
                "configuration": pygments.highlight(
                    yaml.dump(config.get(module_name, {}), default_flow_style=False), lexer, formatter
                ),
            }
        )
    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    with engine.connect() as session:
        out = session.execute(select).partitions(20)

        return {
            "styles": formatter.get_style_defs(),
            "repository": repository,
            "output": out,
            "error": None,
            "issue_url": "",
            "module_configuration": module_config,
        }
