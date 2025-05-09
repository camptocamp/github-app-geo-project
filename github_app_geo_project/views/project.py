"""Output view."""

import datetime
import logging
import os
from typing import TYPE_CHECKING, Any, cast

import pyramid.request
import pyramid.security
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import configuration, models, project_configuration, utils
from github_app_geo_project.module import modules
from github_app_geo_project.templates import pprint_duration, pprint_full_date, pprint_short_date
from github_app_geo_project.views import get_event_loop

if TYPE_CHECKING:
    import githubkit.versions.latest.models

_LOGGER = logging.getLogger(__name__)


def _pprint_date(date: datetime.datetime) -> str:
    short_date = pprint_short_date(date)
    full_date = pprint_full_date(date)
    return f"{short_date} ({full_date})"


def _date_tooltip(job: models.Queue) -> str:
    """Get the tooltip for the date."""
    created = job.created_at
    started = job.started_at
    finished = job.finished_at
    if started is None:
        return f"created:&nbsp;{_pprint_date(created)}<br>not started yet"
    if finished is None:
        return f"created:&nbsp;{_pprint_date(created)}<br>started:&nbsp;{_pprint_date(started)}"
    return f"created:&nbsp;{_pprint_date(created)}<br>started:&nbsp;{_pprint_date(started)}<br>duration:&nbsp;{pprint_duration(finished - started)}"


@view_config(route_name="project", renderer="github_app_geo_project:templates/project.html")  # type: ignore[misc]
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
            "date_tooltip": _date_tooltip,
        }
    config: project_configuration.GithubApplicationProjectConfiguration = {}

    _LOGGER.debug("Configuration: %s", config)

    module_names = set()
    applications: dict[str, dict[str, Any]] = {}
    for app in request.registry.settings["applications"].split():
        applications.setdefault(app, {})
        try:
            if "TEST_APPLICATION" not in os.environ:
                github_project = get_event_loop().run_until_complete(
                    configuration.get_github_project(
                        request.registry.settings,
                        app,
                        owner,
                        repository,
                    ),
                )
                config = get_event_loop().run_until_complete(
                    configuration.get_configuration(
                        github_project,
                    ),
                )
                issues = (
                    get_event_loop()
                    .run_until_complete(
                        github_project.aio_github.rest.issues.async_list_for_repo(
                            owner,
                            repository,
                            state="open",
                            creator=f"{github_project.application.slug}[bot]",
                        ),
                    )
                    .parsed_data
                )
                assert isinstance(issues, list)
                issues = cast("list[githubkit.versions.latest.models.Issue]", issues)
                for issue in issues:
                    if "dashboard" in issue.title.lower().split() and issue.state == "open":
                        applications[app]["issue_url"] = issue.html_url

            module_names.update(request.registry.settings[f"application.{app}.modules"].split())
            for module_name in request.registry.settings[f"application.{app}.modules"].split():
                module = modules.MODULES[module_name]
                if module.required_issue_dashboard():
                    applications[app]["issue_required"] = True

        except:  # pylint: disable=bare-except
            _LOGGER.debug(
                "The repository %s/%s is not installed in the application %s",
                owner,
                repository,
                app,
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
                "configuration": utils.format_yaml(config.get(module_name, {})),  # type: ignore[arg-type]
            },
        )
    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa
    with SessionMaker() as session:
        select_output = session.query(models.Output.id, models.Output.title)
        select_job = session.query(models.Queue)

        if owner == "none":
            select_job = select_job.where(models.Queue.owner.is_(None))
            select_output = select_output.where(models.Output.owner.is_(None))
        elif owner != "all":
            select_job = select_job.where(models.Queue.owner == owner)
            select_output = select_output.where(models.Output.owner == owner)

        if repository == "none":
            select_job = select_job.where(models.Queue.repository.is_(None))
            select_output = select_output.where(models.Output.repository.is_(None))
        elif repository != "all":
            select_job = select_job.where(models.Queue.repository == repository)
            select_output = select_output.where(models.Output.repository == repository)

        if "only_error" in request.params:
            select_output = select_output.where(models.Output.status == models.OutputStatus.ERROR)

        if "status" in request.params:
            select_job = select_job.where(models.Queue.status == request.params["status"])
        if "event_name" in request.params:
            select_job = select_job.where(models.Queue.event_name == request.params["event_name"])
        if "application" in request.params:
            select_job = select_job.where(models.Queue.application == request.params["application"])
        if "module" in request.params:
            select_job = select_job.where(models.Queue.module == request.params["module"])

        select_output = select_output.order_by(models.Output.created_at.desc())
        select_output = select_output.limit(request.params.get("limit", 10))

        select_job = select_job.order_by(models.Queue.created_at.desc())
        select_job = select_job.limit(request.params.get("limit", 20))

        return {
            "styles": "",
            "repository": f"{owner}/{repository}",
            "output": select_output.all(),
            "jobs": select_job.all(),
            "error": None,
            "applications": applications,
            "module_configuration": module_config,
            "date_tooltip": _date_tooltip,
        }
