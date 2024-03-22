"""Webhook view."""

import json
import logging
from typing import Any, NamedTuple

import pyramid.request
import sqlalchemy.engine
import sqlalchemy.orm
from pyramid.view import view_config

from github_app_geo_project import configuration, models, module, utils
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)

# curl -X POST http://localhost:9120/webhook/generic -d '{"repository":{"full_name": "sbrunner/test-github-app"}}'


@view_config(route_name="webhook", renderer="json")  # type: ignore
def webhook(request: pyramid.request.Request) -> dict[str, None]:
    """Receive GitHub application webhook URL."""
    application = request.matchdict["application"]
    data = request.json
    github_objects = configuration.get_github_objects(request.registry.settings, application)

    _LOGGER.debug("Webhook received for %s, with:\n%s", application, json.dumps(data, indent=2))

    if not github_objects.integration.get_app().id != int(data.get("installation", {}).get("id", 0)):
        _LOGGER.error(
            "Invalid installation id %i != %i on %s",
            github_objects.integration.get_app().id,
            data.get("installation", {}).get("id", 0),
            request.url,
        )
        return {}

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
    owner, repository = data["repository"]["full_name"].split("/")
    github = configuration.get_github_application(request.registry.settings, application, owner, repository)

    if data.get("action") == "edited" and "issue" in data:
        if data["issue"]["user"]["login"] == github_objects.integration.get_app().slug + "[bot]":
            repository_full = f"{owner}/{repository}"
            repository = github.application.get_repo(repository_full)
            open_issues = repository.get_issues(
                state="open", creator=github_objects.integration.get_app().owner
            )

            if open_issues.totalCount > 0 and open_issues[0].number == data["issue"]["number"]:
                _LOGGER.debug("Dashboard issue edited")
                old_content = data.get("changes", {}).get("body", {}).get("from", "")
                new_content = data["issue"]["body"]
                session_factory = request.registry["dbsession_factory"]
                engine = session_factory.rw_engine
                with engine.connect() as session:
                    process_dashboard_issue(
                        ProcessContext(github, request.registry.settings, "dashboard", data, session),
                        old_content,
                        new_content,
                    )
                    session.commit()

                return {}

    session_factory = request.registry["dbsession_factory"]
    engine = session_factory.rw_engine
    with engine.connect() as session:
        process_event(
            ProcessContext(
                github,
                request.registry.settings,
                request.headers.get("X-GitHub-Event", "undefined"),
                data,
                session,
            )
        )
        session.commit()
    return {}


class ProcessContext(NamedTuple):
    """The context of the process."""

    # The github application name
    github: configuration.GithubApplication
    # The application configuration
    application_config: dict[str, Any]
    # The event name present in the X-GitHub-Event header
    event_name: str
    # The event data
    event_data: dict[str, Any]
    # The session to be used
    session: sqlalchemy.orm.Session


def process_dashboard_issue(
    context: ProcessContext,
    old_data: str,
    new_data: str,
) -> None:
    """Process changes on the dashboard issue."""
    for name in context.application_config.get(
        f"application.{context.github.objects.name}.modules", ""
    ).split():
        current_module = modules.MODULES.get(name)
        if current_module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        module_old = utils.get_dashboard_issue_module(old_data, name)
        module_new = utils.get_dashboard_issue_module(new_data, name)
        if module_old != module_new:
            _LOGGER.debug("Dashboard issue edited for module %s: %s", name, current_module.title())
            if current_module.required_issue_dashboard():
                for action in current_module.get_actions(
                    module.GetActionContext(
                        github=context.github,
                        event_name="dashboard",
                        event_data={
                            "type": "dashboard",
                            "old_data": module_old,
                            "new_data": module_new,
                        },
                    )
                ):
                    context.session.execute(
                        sqlalchemy.insert(models.Queue).values(
                            {
                                "priority": action.priority,
                                "application": context.github.objects.name,
                                "owner": context.github.owner,
                                "repository": context.github.repository,
                                "event_name": "dashboard",
                                "event_data": {
                                    "type": "dashboard",
                                    "old_data": module_old,
                                    "new_data": module_new,
                                },
                                "module": name,
                                "module_data": action.data,
                            }
                        )
                    )


def process_event(context: ProcessContext) -> None:
    """Process the event."""
    _LOGGER.debug("Processing event for application %s", context.github.objects.name)
    for name in context.application_config.get(
        f"application.{context.github.objects.name}.modules", ""
    ).split():
        current_module = modules.MODULES.get(name)
        if current_module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        _LOGGER.info(
            "Getting actions for the application: %s, repository: %s/%s, module: %s",
            context.github.objects.name,
            context.github.owner,
            context.github.repository,
            name,
        )
        try:
            for action in current_module.get_actions(
                module.GetActionContext(
                    event_name=context.event_name,
                    event_data=context.event_data,
                    github=context.github,
                )
            ):
                context.session.execute(
                    sqlalchemy.insert(models.Queue).values(
                        {
                            "priority": action.priority,
                            "application": context.github.objects.name,
                            "owner": context.github.owner,
                            "repository": context.github.repository,
                            "event_name": context.event_name,
                            "event_data": context.event_data,
                            "module": name,
                            "module_data": action.data,
                        }
                    )
                )
        except Exception as exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error while getting actions for %s: %s", name, exception)
