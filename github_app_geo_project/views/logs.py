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


@view_config(route_name="logs", renderer="github_app_geo_project:templates/logs.html")  # type: ignore
def logs_view(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the logs of a job."""
    title = f"Logs of job {request.matchdict['id']}"
    logs = "Element not found"
    has_access = True

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa
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
                logs = job.log
            else:
                request.response.status = 302
                logs = "Access Denied"
            return {
                "title": title,
                "logs": logs,
                "job": job,
                "reload": job.status in [models.JobStatus.NEW, models.JobStatus.PENDING],
                "favicon_postfix": (
                    "red"
                    if job.status == models.JobStatus.ERROR
                    else ("green" if job.status == models.JobStatus.DONE else "blue")
                ),
            }
        else:
            request.response.status = 404
            return {
                "title": title,
                "logs": logs,
                "reload": False,
                "favicon_postfix": "red",
            }
