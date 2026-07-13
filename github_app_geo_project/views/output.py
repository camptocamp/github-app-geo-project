"""Output view."""

import logging
from typing import Annotated, Any

import sqlalchemy
from fastapi import Depends, Request

from github_app_geo_project import models
from github_app_geo_project.security import User, get_user, has_repo_access

_LOGGER = logging.getLogger(__name__)


async def output_view(
    request: Request,
    output_id: int,
    user: Annotated[User, Depends(get_user)],
) -> dict[str, Any]:
    """Render the output page."""
    title = str(output_id)
    data: list[str | models.OutputData] = ["Element not found"]
    has_access = True

    async with request.app.state.async_session_factory() as session:
        result = await session.execute(
            sqlalchemy.select(models.Output).where(models.Output.id == output_id),
        )
        out = result.scalar()
        if out is not None:
            has_access = await has_repo_access(user, out.owner, out.repository)
            if has_access:
                title = out.title
                data = out.data
            else:
                data = ["Access Denied"]
        else:
            has_access = False

        status_code = 200
        if not has_access and out is not None:
            status_code = 302
        elif out is None:
            status_code = 404

        return {
            "request": request,
            "user": user,
            "title": title,
            "output": data,
            "enumerate": enumerate,
            "status_code": status_code,
        }


OutputData = Annotated[dict[str, Any], Depends(output_view)]
