"""Webhook view."""

import logging
from typing import Annotated

import githubkit.webhooks
import sqlalchemy
from fastapi import Depends, HTTPException, Request

from github_app_geo_project import models, module
from github_app_geo_project.security import AuthType, User, get_user
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)
_APPLICATIONS_SLUG: dict[str, str] = {}


async def webhook(
    request: Request,
    application: str,
    user: Annotated[User, Depends(get_user)],
) -> dict[str, None]:
    """Handle incoming webhooks."""
    if user.auth_type != AuthType.GITHUB_WEBHOOK:
        raise HTTPException(status_code=403, detail="Invalid webhook signature")
    data = await request.json()

    event_name = request.headers.get("X-GitHub-Event", "undefined")
    _LOGGER.debug(
        "Webhook received for %s on %s",
        event_name,
        application,
    )

    if application not in _APPLICATIONS_SLUG:
        # triggering_actor can also be used to avoid infinite event loop
        app_config = settings.application_configs.get(application)
        if app_config is None:
            message = f"Application {application} not found"
            raise ValueError(message)
        try:
            private_key = app_config.github_app.private_key
            application_id = app_config.github_app.id

            aio_auth = githubkit.AppAuthStrategy(application_id, private_key)
            aio_github = githubkit.GitHub(aio_auth)
            aio_application_response = await aio_github.rest.apps.async_get_authenticated()
            aio_application = aio_application_response.parsed_data
            assert aio_application is not None
            if isinstance(aio_application.slug, str):
                _APPLICATIONS_SLUG[application] = aio_application.slug
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Event from the application itself, this can be source of infinite event loop",
            )

    if (
        application in _APPLICATIONS_SLUG
        and data.get("sender", {}).get("login") == _APPLICATIONS_SLUG[application] + "[bot]"
    ):
        _LOGGER.warning(
            "Event from the application itself, this can be source of infinite event loop",
        )

    if "account" in data.get("installation", {}):
        if "repositories" in data:
            _LOGGER.info(
                "Installation event on '%s' with repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["name"] for repo in data["repositories"]]),
            )
        if data.get("repositories_added", []):
            _LOGGER.info(
                "Installation event on '%s' with added repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["full_name"] for repo in data["repositories_added"]]),
            )
        if data.get("repositories_removed", []):
            _LOGGER.info(
                "Installation event on '%s' with removed repositories:\n%s",
                data["installation"]["account"]["login"],
                "\n".join([repo["full_name"] for repo in data["repositories_removed"]]),
            )
        return {}
    owner, repository_name = data["repository"]["full_name"].split("/")

    async with request.app.state.async_session_factory() as session:
        if event_name == "issues":
            event = githubkit.webhooks.parse_obj("issues", data)
            if event.action == "edited" and event.issue and "dashboard" in event.issue.title:
                await session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": module.PRIORITY_HIGH,
                            "application": application,
                            "owner": owner,
                            "repository": repository_name,
                            "github_event_name": "dashboard",
                            "github_event_data": data,
                            "module_event_name": "dashboard",
                            "module_event_data": {
                                "type": "dashboard",
                            },
                        },
                    ),
                )

        _LOGGER.debug("Processing event for application %s", application)
        job = models.Queue()
        job.priority = 0
        job.application = application
        job.owner = owner
        job.repository = repository_name
        job.github_event_name = event_name
        job.github_event_data = data
        job.module = "dispatcher"
        job.module_event_name = event_name
        job.module_event_data = {
            "modules": settings.application_configs[application].modules
            if application in settings.application_configs
            else [],
        }
        session.add(job)
        await session.commit()
    return {}


WebhookData = Annotated[dict[str, None], Depends(webhook)]
