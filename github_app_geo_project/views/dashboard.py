"""Dashboard view."""

import logging
from typing import Annotated, Any

import anyio
import sqlalchemy
from fastapi import Depends, HTTPException, Request
from jinja2 import Environment, FileSystemLoader, select_autoescape

from github_app_geo_project import models, module
from github_app_geo_project.module import modules
from github_app_geo_project.security import User, require_admin
from github_app_geo_project.utils import HTML_FORMATTER

_LOGGER = logging.getLogger(__name__)


async def _render_template(renderer: str, data: dict[str, Any]) -> str:
    package, path = renderer.split(":", 1)
    package_dir = anyio.Path(__file__).parent.parent.parent / package
    template_path = package_dir / path
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(),
    )
    template = env.get_template(template_path.name)
    return template.render(data)


async def dashboard(
    request: Request,
    module_name: str,
    user: Annotated[User, Depends(require_admin)],
) -> dict[str, Any]:
    """Render the dashboard for a module."""
    if module_name not in modules.MODULES:
        raise HTTPException(status_code=404, detail=f"The module {module_name} does not exist")
    module_instance = modules.MODULES[module_name]

    async with request.app.state.async_session_factory() as session:
        module_status = (
            await session.execute(
                sqlalchemy.select(models.ModuleStatus.data).where(models.ModuleStatus.module == module_name),
            )
        ).scalar()
        if module_status is None:
            module_status = {}

        output = module_instance.get_transversal_dashboard(
            module.TransversalDashboardContext(
                module_instance.transversal_status_from_json(module_status or {}),
                dict(request.query_params),
            ),
        )
        data = output.data

        data.setdefault("title", module_instance.title())
        data.setdefault("styles", HTML_FORMATTER.get_style_defs())

        if output.renderer:
            data["html"] = await _render_template(output.renderer, data)

    return {
        "request": request,
        "user": user,
        "title": data["title"],
        "html": data.get("html", ""),
        "styles": data["styles"],
    }


DashboardData = Annotated[dict[str, Any], Depends(dashboard)]
