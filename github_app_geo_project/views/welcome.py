"""Output view."""

import logging
from typing import Any

import pyramid.httpexceptions
import pyramid.request
import pyramid.response
import pyramid.security
from pyramid.view import view_config

from github_app_geo_project import configuration

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="welcome", renderer="github_app_geo_project:templates/welcome.html")  # type: ignore[misc]
def output(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the welcome page."""
    del request  # Unused

    return {
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "start_url": configuration.APPLICATION_CONFIGURATION["start-url"],
        "projects": [],
    }
