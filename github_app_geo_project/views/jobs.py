"""Jobs view - list jobs across repositories."""

import datetime
import logging
from typing import Annotated, Any

import sqlalchemy
from fastapi import Depends, Query, Request

from github_app_geo_project import models
from github_app_geo_project.security import User, get_user, has_repo_access
from github_app_geo_project.templates import pprint_duration, pprint_full_date, pprint_short_date
from github_app_geo_project.utils import HTML_FORMATTER

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

    return (
        f"created:&nbsp;{_pprint_date(created)}<br>started:&nbsp;{_pprint_date(started)}"
        f"<br>duration:&nbsp;{pprint_duration(finished - started)}"
    )


async def jobs_view(
    request: Request,
    user: Annotated[User, Depends(get_user)],
    owner: str | None = Query(None, description="Filter by owner"),
    repository: str | None = Query(None, description="Filter by repository"),
    status: str | None = Query(None, description="Filter jobs by status"),
    github_event_name: str | None = Query(None, description="Filter jobs by GitHub event name"),
    module_event_name: str | None = Query(None, description="Filter jobs by module event name"),
    application: str | None = Query(None, description="Filter jobs by application"),
    module: str | None = Query(None, description="Filter jobs by module"),
    limit: int = Query(50, description="Number of jobs to show"),
) -> dict[str, Any]:
    """Render the jobs page listing jobs across repositories."""
    if owner and repository:
        if not await has_repo_access(user, owner, repository):
            return {
                "request": request,
                "user": user,
                "styles": HTML_FORMATTER.get_style_defs(),
                "jobs": [],
                "owner": owner,
                "repository": repository,
                "error": "Access Denied",
                "date_tooltip": _date_tooltip,
                "show_repository": False,
            }
    elif not user.is_admin:
        return {
            "request": request,
            "user": user,
            "styles": HTML_FORMATTER.get_style_defs(),
            "jobs": [],
            "owner": None,
            "repository": None,
            "error": "Access Denied",
            "date_tooltip": _date_tooltip,
            "show_repository": True,
        }

    show_repository = not bool(owner and repository)

    async with request.app.state.async_session_factory() as session:
        select_job = sqlalchemy.select(models.Queue)

        if owner:
            select_job = select_job.where(models.Queue.owner == owner)
        if repository:
            select_job = select_job.where(models.Queue.repository == repository)
        if status:
            select_job = select_job.where(models.Queue.status == status)
        if github_event_name is not None:
            select_job = select_job.where(models.Queue.github_event_name == github_event_name)
        if module_event_name is not None:
            select_job = select_job.where(models.Queue.module_event_name == module_event_name)
        if application is not None:
            select_job = select_job.where(models.Queue.application == application)
        if module is not None:
            select_job = select_job.where(models.Queue.module == module)

        select_job = select_job.order_by(models.Queue.created_at.desc())
        select_job = select_job.limit(limit)

        jobs_result = (await session.execute(select_job)).scalars().all()

        return {
            "request": request,
            "user": user,
            "styles": HTML_FORMATTER.get_style_defs(),
            "jobs": jobs_result,
            "owner": owner,
            "repository": repository,
            "error": None,
            "date_tooltip": _date_tooltip,
            "show_repository": show_repository,
        }


JobsData = Annotated[dict[str, Any], Depends(jobs_view)]
