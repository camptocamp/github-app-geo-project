"""Output view."""

import logging
from typing import Annotated, Any

import sqlalchemy
from fastapi import Depends, HTTPException, Request

from github_app_geo_project import models
from github_app_geo_project.security import User, get_user, has_repo_access

_LOGGER = logging.getLogger(__name__)


async def output_by_name_view(
    request: Request,
    owner: str,
    repository: str,
    name: str,
    user: Annotated[User, Depends(get_user)],
) -> dict[str, Any]:
    """Fetch output data by owner/repository/name."""

    if not await has_repo_access(user, owner, repository):
        raise HTTPException(status_code=302)

    async with request.app.state.async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.select(models.Output).where(
                models.Output.owner == owner,
                models.Output.repository == repository,
                models.Output.name == name,
            ),
        )

        out = result.scalar()
        if out is None:
            raise HTTPException(status_code=404)

        return {
            "request": request,
            "user": user,
            "title": out.title,
            "renderer": out.renderer,
            "renderer_data": out.renderer_data,
            "enumerate": enumerate,
        }


OutputByNameData = Annotated[dict[str, Any], Depends(output_by_name_view)]
