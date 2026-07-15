"""Project view."""

import logging
from typing import TYPE_CHECKING, Annotated, Any, cast

import sqlalchemy
from fastapi import Depends, Query, Request

from github_app_geo_project import configuration, models, project_configuration, utils
from github_app_geo_project.module import modules
from github_app_geo_project.security import User, get_user, has_repo_access
from github_app_geo_project.settings import settings
from github_app_geo_project.utils import HTML_FORMATTER

if TYPE_CHECKING:
    import githubkit_schemas.latest.models

_LOGGER = logging.getLogger(__name__)


async def project(
    request: Request,
    owner: str,
    repository: str,
    user: Annotated[User, Depends(get_user)],
    only_error: bool = Query(default=False, description="Filter only error outputs"),
    limit: int = Query(10, description="Number of outputs to show"),
) -> dict[str, Any]:
    """Render the project page."""
    if not await has_repo_access(user, owner, repository):
        return {
            "request": request,
            "user": user,
            "styles": HTML_FORMATTER.get_style_defs(),
            "repository": f"{owner}/{repository}",
            "output": [],
            "error": "Access Denied",
            "applications": {},
            "module_configuration": [],
            "owner": owner,
            "repository_name": repository,
        }
    config: project_configuration.GithubApplicationProjectConfiguration = {}

    _LOGGER.debug("Configuration: %s", config)

    module_names: set[str] = set()
    applications: dict[str, dict[str, Any]] = {}
    for app_name, app_config in settings.application_configs.items():
        applications.setdefault(app_name, {})
        try:
            if not settings.test.app_name:
                github_project = await configuration.get_github_project(
                    app_name,
                    owner,
                    repository,
                )
                config = await configuration.get_configuration(github_project)
                issues = (
                    await github_project.aio_github.rest.issues.async_list_for_repo(
                        owner,
                        repository,
                        state="open",
                        creator=f"{github_project.application.slug}[bot]",
                    )
                ).parsed_data
                assert isinstance(issues, list)
                issues = cast("list[githubkit_schemas.latest.models.Issue]", issues)
                for issue in issues:
                    if "dashboard" in issue.title.lower().split() and issue.state == "open":
                        applications[app_name]["issue_url"] = issue.html_url

            module_names.update(app_config.modules)
            for module_name in app_config.modules:
                module_instance = modules.MODULES[module_name]
                if module_instance.required_issue_dashboard():
                    applications[app_name]["issue_required"] = True

        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "The repository %s/%s is not installed in the application %s",
                owner,
                repository,
                app_name,
            )

    module_config_list = []
    for module_name in module_names:
        if module_name not in modules.MODULES:
            _LOGGER.error("Unknown module %s", module_name)
            continue
        module_instance = modules.MODULES[module_name]
        module_config_list.append(
            {
                "name": module_name,
                "title": module_instance.title(),
                "description": module_instance.description(),
                "documentation_url": module_instance.documentation_url(),
                "configuration": utils.format_yaml(cast("dict[str, Any]", config.get(module_name, {}))),
            },
        )

    async with request.app.state.async_session_factory() as session:
        select_output = sqlalchemy.select(models.Output.id, models.Output.title)

        if owner == "none":
            select_output = select_output.where(models.Output.owner.is_(None))
        elif owner != "all":
            select_output = select_output.where(models.Output.owner == owner)

        if repository == "none":
            select_output = select_output.where(models.Output.repository.is_(None))
        elif repository != "all":
            select_output = select_output.where(models.Output.repository == repository)

        if only_error:
            select_output = select_output.where(
                models.Output.status == models.OutputStatus.ERROR,
            )

        select_output = select_output.order_by(models.Output.created_at.desc())
        select_output = select_output.limit(limit)

        output_result = (await session.execute(select_output)).all()

        return {
            "request": request,
            "user": user,
            "styles": HTML_FORMATTER.get_style_defs(),
            "repository": f"{owner}/{repository}",
            "output": output_result,
            "error": None,
            "applications": applications,
            "module_configuration": module_config_list,
            "owner": owner,
            "repository_name": repository,
        }


ProjectData = Annotated[dict[str, Any], Depends(project)]
