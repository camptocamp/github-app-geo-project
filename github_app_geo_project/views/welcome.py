"""Welcome view."""

import logging
from typing import Annotated, Any

from fastapi import Depends, Request

from github_app_geo_project import configuration
from github_app_geo_project.security import User, get_user

_LOGGER = logging.getLogger(__name__)


async def welcome(
    request: Request,
    user: Annotated[User, Depends(get_user)],
) -> dict[str, Any]:
    """Render the welcome page."""
    return {
        "request": request,
        "user": user,
        "title": configuration.APPLICATION_CONFIGURATION["title"],
        "start_url": configuration.APPLICATION_CONFIGURATION["start-url"],
        "projects": [],
    }


WelcomeData = Annotated[dict[str, Any], Depends(welcome)]
