"""Output view."""

import logging
import os
from typing import Any

import pyramid.httpexceptions
import pyramid.renderers
import pyramid.request
import pyramid.response
import pyramid.security
import sqlalchemy.sql
from pyramid.view import view_config

from github_app_geo_project import models, module
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


@view_config(route_name="dashboard", renderer="github_app_geo_project:templates/dashboard.html")  # type: ignore[untyped-decorator]
def dashboard(request: pyramid.request.Request) -> dict[str, Any]:
    """Get the dashboard."""
    repository = os.environ["C2C_AUTH_GITHUB_REPOSITORY"]
    user_permission = request.has_permission(
        repository,
        {"github_repository": repository, "github_access_type": "admin"},
    )
    admin = isinstance(user_permission, pyramid.security.Allowed)

    if not admin:
        message = "You are not allowed to access this page"
        raise pyramid.httpexceptions.HTTPForbidden(message)

    module_name = request.matchdict["module"]
    if module_name not in modules.MODULES:
        message = f"The module {module_name} does not exist"
        raise pyramid.httpexceptions.HTTPNotFound(message)
    module_instance = modules.MODULES[module_name]

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.ro_engine
    SessionMaker = sqlalchemy.orm.sessionmaker(engine)  # noqa
    with SessionMaker() as session:
        module_status = session.execute(
            sqlalchemy.sql.select(models.ModuleStatus.data).where(models.ModuleStatus.module == module_name),
        ).scalar()
        if module_status is None:
            module_status = {}
        output = module_instance.get_transversal_dashboard(
            module.TransversalDashboardContext(
                module_instance.transversal_status_from_json(module_status or {}),
                dict(request.params),
            ),
        )
        data = output.data

        if output.renderer:
            data["html"] = pyramid.renderers.render(output.renderer, data)

        data.setdefault("title", module_instance.title())
        data.setdefault("styles", "")
    return data
