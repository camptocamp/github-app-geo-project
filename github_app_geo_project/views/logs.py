"""Output view."""

import logging
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import models

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="logs", renderer="github_app_geo_project:templates/logs.html")  # type: ignore[untyped-decorator]
def logs_view(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the logs of a job."""
    if not request.is_authenticated:
        raise pyramid.httpexceptions.HTTPForbidden

    title = f"Logs of job {request.matchdict['id']}"
    logs: str | None = "Element not found"
    error_message = None
    has_access = True

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa: N806
    with SessionMaker() as session:
        job = session.query(models.Queue).where(models.Queue.id == request.matchdict["id"]).first()
        if job is not None:
            full_repository = f"{job.owner}/{job.repository}"
            permission = request.has_permission(
                full_repository,
                {"github_repository": full_repository, "github_access_type": "admin"},
            )
            has_access = isinstance(permission, pyramid.security.Allowed)
            if has_access:
                log_query = session.query(models.JobLogEntry).where(
                    models.JobLogEntry.job_id == job.id,
                )

                level_filter = request.params.get("level")
                if level_filter:
                    level_map = {
                        "CRITICAL": 50,
                        "ERROR": 40,
                        "WARNING": 30,
                        "INFO": 20,
                        "DEBUG": 10,
                    }
                    level = level_filter.strip().upper()
                    if level:
                        if level not in level_map:
                            message = f"Invalid log level filter: {level_filter}"
                            raise pyramid.httpexceptions.HTTPBadRequest(detail=message)
                        level_no = level_map[level]
                        log_query = log_query.where(models.JobLogEntry.level_no >= level_no)

                filename_filter = request.params.get("filename")
                if filename_filter:
                    log_query = log_query.where(
                        models.JobLogEntry.filename.op("~")(filename_filter),
                    )

                try:
                    log_entries = log_query.order_by(models.JobLogEntry.id).all()
                except (sqlalchemy.exc.DataError, sqlalchemy.exc.ProgrammingError) as exception:
                    _LOGGER.warning(
                        "Invalid filename filter regex for job %s: %s",
                        job.id,
                        exception,
                    )
                    error_message = "Invalid filename filter regex"
                    log_entries = []
                if log_entries:
                    logs = "\n".join(entry.log for entry in log_entries)
                elif level_filter or filename_filter:
                    # Explicit message when filters exclude all entries.
                    logs = "No logs match the current filters."
                elif job.log is not None:
                    # Fallback for legacy jobs that still store logs on the job.
                    logs = job.log
                else:
                    # Ensure logs is never None, even for new jobs without entries yet.
                    logs = ""
            else:
                raise pyramid.httpexceptions.HTTPUnauthorized
            return {
                "title": title,
                "logs": logs,
                "job": job,
                "error_message": error_message,
                "level_filter": level_filter or "",
                "filename_filter": filename_filter or "",
                "reload": job.status_enum in [models.JobStatus.NEW, models.JobStatus.PENDING],
                "favicon_postfix": (
                    "red"
                    if job.status_enum == models.JobStatus.ERROR
                    else ("green" if job.status_enum == models.JobStatus.DONE else "blue")
                ),
            }
        raise pyramid.httpexceptions.HTTPNotFound
