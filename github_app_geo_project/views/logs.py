import logging
import os
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
from pyramid.view import view_config

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="logs", renderer="github_app_geo_project:templates/logs.html")  # type: ignore
def logs(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the logs of a job."""
    self.request.matchdict["id"]
    repo = "camptocamp/c2cgeoportal"

    if "TEST_USER" not in os.environ:
        permission = request.has_permission(repo, {"github_repository": repo, "github_access_type": "pull"})
        if not isinstance(permission, pyramid.security.Allowed):
            raise pyramid.httpexceptions.HTTPForbidden()

    return {}
