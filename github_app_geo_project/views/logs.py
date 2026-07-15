"""Logs view."""

import logging
import re
from typing import Annotated, Any

import sqlalchemy
from fastapi import Depends, HTTPException, Query, Request

from github_app_geo_project import models, utils
from github_app_geo_project.security import User, get_user, has_repo_access
from github_app_geo_project.utils import HTML_FORMATTER

_LOGGER = logging.getLogger(__name__)
_LEVEL_RE = re.compile("^[0-9]+$")


async def logs_view(
    request: Request,
    logs_id: int,
    user: Annotated[User, Depends(get_user)],
    level: str = Query("", description="Filter logs by minimum level number"),
    filename: str | None = Query(None, description="Filter logs by filename regex"),
) -> dict[str, Any]:
    """Render the logs page."""
    logs: str | None = "Element not found"
    error_messages: list[str] = []
    title = f"Logs of job {logs_id}"

    async with request.app.state.async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.select(models.Queue).where(models.Queue.id == logs_id),
        )
        job = result.scalar()
        if job is not None:
            has_access = await has_repo_access(user, job.owner, job.repository)
            if not has_access:
                return {
                    "request": request,
                    "user": user,
                    "styles": HTML_FORMATTER.get_style_defs(),
                    "title": "Access Denied",
                    "logs": "Access Denied",
                    "job": job,
                    "error_message": "Access Denied",
                    "level_filter": "",
                    "filename_filter": "",
                    "reload": False,
                    "favicon_postfix": "red",
                }
            log_query = sqlalchemy.select(models.JobLogEntry).where(
                models.JobLogEntry.job_id == job.id,
            )

            if _LEVEL_RE.match(level):
                log_query = log_query.where(models.JobLogEntry.level_no >= int(level))
            elif level:
                error_messages.append("Invalid level filter")

            if filename:
                log_query = log_query.where(
                    models.JobLogEntry.filename.op("~")(filename),
                )

            if not error_messages:
                try:
                    log_entries_result = await session.execute(
                        log_query.order_by(models.JobLogEntry.id),
                    )
                    log_entries = log_entries_result.scalars().all()
                except (sqlalchemy.exc.DataError, sqlalchemy.exc.ProgrammingError) as exception:
                    _LOGGER.info(
                        "Invalid filename filter regex for job %s: %s",
                        job.id,
                        exception,
                    )
                    error_messages.append("Invalid filename filter regex")
                    log_entries = []
            else:
                log_entries = []

            css_style = None
            if log_entries:
                css_style_set: set[str] = set()
                for entry in log_entries:
                    if entry.css_style:
                        css_style_set.add(entry.css_style)
                css_style = utils.merge_css_blocks(css_style_set)
                logs = "\n".join(entry.log for entry in log_entries)
            elif level or filename:
                # Explicit message when filters exclude all entries.
                logs = "No logs match the current filters."
            elif job.log is not None:
                # Fallback for legacy jobs that still store logs on the job.
                logs = job.log
            else:
                # Ensure logs is never None, even for new jobs without entries yet.
                logs = ""

            return {
                "request": request,
                "user": user,
                "styles": HTML_FORMATTER.get_style_defs()
                + (f"\n\n/* styles form log lines */\n{css_style}" if css_style else ""),
                "title": title,
                "logs": logs,
                "job": job,
                "error_message": "<br />".join(error_messages),
                "level_filter": level,
                "filename_filter": filename or "",
                "reload": job.status_enum in [models.JobStatus.NEW, models.JobStatus.PENDING],
                "favicon_postfix": (
                    "red"
                    if job.status_enum == models.JobStatus.ERROR
                    else ("green" if job.status_enum == models.JobStatus.DONE else "blue")
                ),
            }
        raise HTTPException(status_code=404)


LogsData = Annotated[dict[str, Any], Depends(logs_view)]
