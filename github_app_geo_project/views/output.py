"""Output view."""

import logging
import os
from typing import Any

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
    out = models.DBSession.execute(
        sqlalchemy.select(models.Output).where(models.Output.id == request.matchdict["id"])
    ).first()
    if out is None:
        raise pyramid.httpexceptions.HTTPNotFound()

    if "TEST_USER" not in os.environ:
        permission = request.has_permission(
            out.repository,
            {"github_repository": out.repository, "github_access_type": out.access_type},
        )
        if not isinstance(permission, pyramid.security.Allowed):
            raise pyramid.httpexceptions.HTTPForbidden()

    return {
        "title": out.title,
        "output": out.data,
    }
