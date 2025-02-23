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


@view_config(route_name="output", renderer="github_app_geo_project:templates/output.html")  # type: ignore[misc]
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the output of a job."""
    title = request.matchdict["id"]
    data: list[str | models.OutputData] = ["Element not found"]
    has_access = True

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa
    with SessionMaker() as session:
        out = session.query(models.Output).where(models.Output.id == request.matchdict["id"]).first()
        if out is not None:
            full_repository = f"{out.owner}/{out.repository}"
            permission = request.has_permission(
                full_repository,
                {"github_repository": full_repository, "github_access_type": out.access_type},
            )
            has_access = isinstance(permission, pyramid.security.Allowed)
            if has_access:
                title = out.title
                data = out.data
            else:
                request.response.status = 302
                data = ["Access Denied"]
        else:
            request.response.status = 404

        return {
            "title": title,
            "output": data,
            "enumerate": enumerate,
        }
