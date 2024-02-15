"""Output view."""

import logging
from typing import Any

import c2cwsgiutils.auth
import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy
from pyramid.view import view_config

from github_app_geo_project import models

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="output", renderer="github_app_geo_project:templates/output.html")  # type: ignore
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the output of a job."""
    title = request.matchdict["id"]
    data = "Element not found"
    has_access = True

    out = models.DBSession.execute(
        sqlalchemy.select(models.Output).where(models.Output.id == request.matchdict["id"])
    ).one()
    if out is not None:
        permission = request.has_permission(
            out.repository,
            {"github_repository": out.repository, "github_access_type": out.access_type},
        )
        has_access = isinstance(permission, pyramid.security.Allowed)
        if has_access:
            title = out.title
            data = out.data
        else:
            data = "Access Denied"

    return {
        "title": title,
        "output": data,
    }
