"""Output view."""

import datetime
import logging
import os
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

from github_app_geo_project import configuration, models, project_configuration
from github_app_geo_project.module import modules
from github_app_geo_project.templates import pprint_date, pprint_duration

_LOGGER = logging.getLogger(__name__)


def _date_tooltip(job: list[datetime.datetime]) -> str:
    """Get the tooltip for the date."""
    created = job[4]
    started = job[5]
    finished = job[7]
    if started is None:
        return f"created:&nbsp;{pprint_date(created)}<br>not started yet"
    if finished is None:
        return f"created:&nbsp;{pprint_date(created)}<br>started:&nbsp;{pprint_date(started)}"
    return f"created:&nbsp;{pprint_date(created)}<br>started:&nbsp;{pprint_date(started)}<br>duration:&nbsp;{pprint_duration(finished - started)}"


@view_config(route_name="project", renderer="github_app_geo_project:templates/project.html")  # type: ignore
def project(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the output of a job."""
    owner = request.matchdict["owner"]
    repository = request.matchdict["repository"]
    permission = request.has_permission(
        f"{owner}/{repository}",
        {"github_repository": f"{owner}/{repository}", "github_access_type": "admin"},
    )
    has_access = isinstance(permission, pyramid.security.Allowed)
    if not has_access:
        return {
            "styles": "",
            "repository": f"{owner}/{repository}",
            "output": [],
            "error": "Access Denied",
            "issue_url": "",
            "issue_required": False,
            "module_configuration": [],
            "jobs": [],
            "applications": {},
        }
    config: project_configuration.GithubApplicationProjectConfiguration = {}

    _LOGGER.debug("Configuration: %s", config)

    lexer = pygments.lexers.YamlLexer()
    formatter = pygments.formatters.HtmlFormatter(style="github-dark")

    select_output = (
        sqlalchemy.select(models.Output.id, models.Output.title)
        .where(
            models.Output.owner == owner,
            models.Output.repository == repository,
        )
        .order_by(models.Output.created_at.desc())
    )
    if "only_error" in request.params:
        select_output = select_output.where(models.Output.status == models.OutputStatus.ERROR)

    select_job = (
        sqlalchemy.select(
            models.Queue.id,
            models.Queue.status,
            models.Queue.application,
            models.Queue.module,
            models.Queue.created_at,
            models.Queue.started_at,
            models.Queue.event_name,
            models.Queue.finished_at,
            models.Queue.log,
        )
        .where(
            models.Queue.owner == owner,
            models.Queue.repository == repository,
        )
        .order_by(models.Queue.created_at.desc())
    )

    module_names = set()
    applications: dict[str, dict[str, Any]] = {}
    for app in request.registry.settings["applications"].split():
        applications.setdefault(app, {})
        try:
            if "TEST_APPLICATION" not in os.environ:
                config = configuration.get_configuration(
                    request.registry.settings,
                    owner,
                    repository,
                    app,
                )
                github_project = configuration.get_github_project(
                    request.registry.settings,
                    app,
                    owner,
                    repository,
                )
                repo = github_project.github.get_repo(f"{owner}/{repository}")
                for issue in repo.get_issues(
                    state="open",
                    creator=f"{github_project.application.integration.get_app().slug}[bot]",  # type: ignore[arg-type]
                ):
                    if "dashboard" in issue.title.lower().split() and issue.state == "open":
                        applications[app]["issue_url"] = issue.html_url

            module_names.update(request.registry.settings[f"application.{app}.modules"].split())
            for module_name in request.registry.settings[f"application.{app}.modules"].split():
                module = modules.MODULES[module_name]
                if module.required_issue_dashboard():
                    applications[app]["issue_required"] = True

        except:  # nosec, pylint: disable=bare-except
            _LOGGER.debug(
                "The repository %s/%s is not installed in the application %s", owner, repository, app
            )
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
        return {
            "styles": formatter.get_style_defs(),
            "repository": f"{owner}/{repository}",
            "output": session.execute(select_output.limit(10)).all(),
            "jobs": session.execute(select_job.limit(20)).all(),
            "error": None,
            "applications": applications,
            "module_configuration": module_config,
            "date_tooltip": _date_tooltip,
        }
