"""Project view."""

import datetime
import logging
from typing import TYPE_CHECKING, Annotated, Any, cast

import sqlalchemy
from fastapi import Depends, HTTPException, Query, Request

from github_app_geo_project import configuration, models, project_configuration, utils
from github_app_geo_project.module import modules
from github_app_geo_project.security import User, get_user, has_repo_access
from github_app_geo_project.settings import settings
from github_app_geo_project.templates import pprint_duration, pprint_full_date, pprint_short_date
from github_app_geo_project.utils import HTML_FORMATTER

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


async def project(
    request: Request,
    owner: str,
    repository: str,
    user: Annotated[User, Depends(get_user)],
    only_error: bool = Query(default=False, description="Filter only error outputs"),
    status: str | None = Query(None, description="Filter jobs by status"),
    github_event_name: str | None = Query(None, description="Filter jobs by GitHub event name"),
    module_event_name: str | None = Query(None, description="Filter jobs by module event name"),
    application: str | None = Query(None, description="Filter jobs by application"),
    module: str | None = Query(None, description="Filter jobs by module"),
    limit: int = Query(10, description="Number of outputs to show"),
    job_limit: int = Query(20, description="Number of jobs to show"),
) -> dict[str, Any]:
    """Render the project page."""
    if not await has_repo_access(user, owner, repository):
        raise HTTPException(status_code=403, detail="Access denied")
    config: project_configuration.GithubApplicationProjectConfiguration = {}

    _LOGGER.debug("Configuration: %s", config)

    module_names: set[str] = set()
    applications: dict[str, dict[str, Any]] = {}
    for app_name, app_config in settings.application_configs.items():
        applications.setdefault(app_name, {})
        try:
            if not settings.test.app_name:
                github_project = await configuration.get_github_project(
                    app_name,
                    owner,
                    repository,
                )
                config = await configuration.get_configuration(github_project)
                issues = (
                    await github_project.aio_github.rest.issues.async_list_for_repo(
                        owner,
                        repository,
                        state="open",
                        creator=f"{github_project.application.slug}[bot]",
                    )
                ).parsed_data
                assert isinstance(issues, list)
                issues = cast("list[githubkit.versions.latest.models.Issue]", issues)
                for issue in issues:
                    if "dashboard" in issue.title.lower().split() and issue.state == "open":
                        applications[app_name]["issue_url"] = issue.html_url

            module_names.update(app_config.modules)
            for module_name in app_config.modules:
                module_instance = modules.MODULES[module_name]
                if module_instance.required_issue_dashboard():
                    applications[app_name]["issue_required"] = True

        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "The repository %s/%s is not installed in the application %s",
                owner,
                repository,
                app_name,
            )

    module_config_list = []
    for module_name in module_names:
        if module_name not in modules.MODULES:
            _LOGGER.error("Unknown module %s", module_name)
            continue
        module_instance = modules.MODULES[module_name]
        module_config_list.append(
            {
                "name": module_name,
                "title": module_instance.title(),
                "description": module_instance.description(),
                "documentation_url": module_instance.documentation_url(),
                "configuration": utils.format_yaml(cast("dict[str, Any]", config.get(module_name, {}))),
            },
        )

    async with request.app.state.async_session_factory() as session:
        select_output = sqlalchemy.select(models.Output.id, models.Output.title)
        select_job = sqlalchemy.select(models.Queue)

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

        if only_error:
            select_output = select_output.where(
                models.Output.status == models.OutputStatus.ERROR,
            )

        if status is not None:
            select_job = select_job.where(models.Queue.status == status)
        if github_event_name is not None:
            select_job = select_job.where(models.Queue.github_event_name == github_event_name)
        if module_event_name is not None:
            select_job = select_job.where(models.Queue.module_event_name == module_event_name)
        if application is not None:
            select_job = select_job.where(models.Queue.application == application)
        if module is not None:
            select_job = select_job.where(models.Queue.module == module)

        select_output = select_output.order_by(models.Output.created_at.desc())
        select_output = select_output.limit(limit)

        select_job = select_job.order_by(models.Queue.created_at.desc())
        select_job = select_job.limit(job_limit)

        output_result = (await session.execute(select_output)).all()
        jobs_result = (await session.execute(select_job)).scalars().all()

        return {
            "request": request,
            "user": user,
            "styles": HTML_FORMATTER.get_style_defs(),
            "repository": f"{owner}/{repository}",
            "output": output_result,
            "jobs": jobs_result,
            "error": None,
            "applications": applications,
            "module_configuration": module_config_list,
            "date_tooltip": _date_tooltip,
        }


ProjectData = Annotated[dict[str, Any], Depends(project)]
