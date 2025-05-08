"""Module to dispatch publishing event."""

import logging
import os
from typing import Any

import githubkit.webhooks
import sqlalchemy
from pydantic import BaseModel

from github_app_geo_project import models, module
from github_app_geo_project.module import modules
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class _EventData(BaseModel):
    modules: list[str] = []
    """The list of modules to dispatch the event to."""


class Dispatcher(module.Module[None, _EventData, None, None]):
    """
    The event dispatcher module.

    Dispatch webhook and cli event across the repository and modules.
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Dispatcher"

    def description(self) -> str:
        """Get the description of the module."""
        return "Manage the webhook events"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Webhook"

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the configuration."""
        return {}

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={},
            events=set(),
        )

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_EventData]]:
        """
        Get the actions of the module.

        Not needed for this module
        """
        del context
        return []

    async def process(
        self,
        context: module.ProcessContext[None, _EventData],
    ) -> module.ProcessOutput[_EventData, None]:
        """
        Process the action.
        """

        if context.event_name == "event":
            await _process_event(context)
            return module.ProcessOutput(output={"summary": "Event processed"})

        if context.event_name == "repo_event":
            await _process_repo_event(context)
            return module.ProcessOutput(output={"summary": "Event processed on repository"})

        re_requested_check_ids = _get_re_requested_check_suite_id(
            context.event_name,
            context.event_data,
        )
        outputs, nb_re_run = (
            await _re_requested_check_suite(context, *re_requested_check_ids)
            if re_requested_check_ids is not None
            else ([], 0)
        )
        if not outputs:
            outputs.append("")

        process_event_outputs, nb_modules_action = await process_event(context)
        if not process_event_outputs:
            process_event_outputs.append("No action to process from other modules")
        return module.ProcessOutput(
            output={
                "title": f"Dispatch {context.event_name} events to modules and do check re-runs",
                "summary": f"Create {nb_modules_action} actions and performed {nb_re_run} check re-runs",
                "text": "\n".join([*outputs, "", *process_event_outputs]),
            },
        )

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False


async def process_event(context: module.ProcessContext[None, _EventData]) -> tuple[list[str], int]:
    """Process the event."""
    owner = "camptocamp" if "TEST_APPLICATION" in os.environ else context.github_project.owner
    repository = "test" if "TEST_APPLICATION" in os.environ else context.github_project.repository
    application = (
        os.environ["TEST_APPLICATION"]  # noqa: SIM401
        if "TEST_APPLICATION" in os.environ
        else context.github_project.application.name
    )
    _LOGGER.debug("Processing event for application %s", application)
    outputs = []
    number = 0
    for name in context.module_event_data.modules:
        current_module = modules.MODULES.get(name)
        if current_module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        _LOGGER.info(
            "Getting actions for the repository: %s/%s, module: %s",
            owner,
            repository,
            name,
        )
        try:
            for action in current_module.get_actions(
                module.GetActionContext(
                    event_name=context.event_name,
                    event_data=context.event_data,
                    owner=owner,
                    repository=repository,
                    github_application=context.github_project.application if context.github_project else None,
                ),
            ):
                _LOGGER.info(
                    "Got action %s",
                    action.title or "Untitled",
                )
                outputs.append(f"Create job for module {name} with action {action.title or 'Untitled'}")
                number += 1
                priority = action.priority if action.priority >= 0 else module.PRIORITY_STANDARD
                event_name = action.title or context.event_name
                module_data = current_module.event_data_to_json(action.data)

                jobs_unique_on = current_module.jobs_unique_on()
                if jobs_unique_on:
                    update = (
                        sqlalchemy.update(models.Queue)
                        .where(models.Queue.status == models.JobStatus.NEW.name)
                        .where(models.Queue.application == application)
                        .where(models.Queue.module == name)
                    )
                    for key in jobs_unique_on:
                        if key == "priority":
                            update = update.where(models.Queue.priority == priority)
                        elif key == "owner":
                            update = update.where(models.Queue.owner == owner)
                        elif key == "repository":
                            update = update.where(
                                models.Queue.repository == repository,
                            )
                        elif key == "event_name":
                            update = update.where(models.Queue.event_name == event_name)
                        elif key == "event_data":
                            update = update.where(
                                sqlalchemy.cast(models.Queue.event_data, sqlalchemy.TEXT)
                                == sqlalchemy.cast(context.event_data, sqlalchemy.TEXT),
                            )
                        elif key == "module_data":
                            update = update.where(
                                sqlalchemy.cast(models.Queue.module_data, sqlalchemy.TEXT)
                                == sqlalchemy.cast(module_data, sqlalchemy.TEXT),
                            )
                        else:
                            _LOGGER.error("Unknown jobs_unique_on key: %s", key)

                    update = update.values(
                        {
                            "status": models.JobStatus.SKIPPED.name,
                        },
                    )

                    await context.session.execute(update)

                job = models.Queue()
                job.priority = priority
                job.application = application
                job.owner = owner
                job.repository = repository
                job.event_name = event_name
                job.event_data = context.event_data
                job.module = name
                job.module_data = module_data
                context.session.add(job)
                await context.session.flush()
                github_project = None

                should_create_checks = action.checks
                if should_create_checks is None:
                    # Auto (major of event that comes from GitHub)
                    should_create_checks = context.event_name in [
                        "pull_request",
                        "pusher",
                        "check_run",
                        "check_suite",
                        "workflow_run",
                    ]
                if should_create_checks and github_project is not None:
                    await module_utils.create_checks(
                        job,
                        context.session,
                        current_module,
                        github_project,
                        context.service_url,
                    )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error while getting actions for %s", name)
    await context.session.commit()
    return outputs, number


def _get_re_requested_check_suite_id(
    event_name: str,
    event_data: dict[str, Any],
) -> tuple[int, int | None] | None:
    """
    Check if the event is a rerequested event and return relevant IDs.

    Arguments
    ---------
        event_name: The name of the event.
        event_data: The data associated with the event.

    Returns
    -------
        tuple[int, int] | None: A tuple containing:
            - check_suite_id (int): The ID of the check suite.
            - check_run_id (int): The ID of the check run.
        Returns None if the event is not a rerequested check_run event or if the required data is missing.
    """
    if event_name == "check_run":
        event_data_check_run = githubkit.webhooks.parse_obj("check_run", event_data)
        if event_data_check_run.action == "rerequested" and event_data_check_run.check_run.check_suite.id:
            return event_data_check_run.check_run.check_suite.id, event_data_check_run.check_run.id
    elif event_name == "check_suite":
        event_data_check_suite = githubkit.webhooks.parse_obj("check_suite", event_data)
        if event_data_check_suite.action == "rerequested":
            return event_data_check_suite.check_suite.id, None
    return None


async def _re_requested_check_suite(
    context: module.ProcessContext[None, _EventData],
    check_suite_id: int,
    check_run_id: int | None,
) -> tuple[list[str], int]:
    outputs = []
    number = 0
    assert context.github_project is not None
    try:
        check_suite = (
            await context.github_project.aio_github.rest.checks.async_get_suite(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                check_suite_id=check_suite_id,
            )
        ).parsed_data
        check_runs = (
            await context.github_project.aio_github.rest.checks.async_list_for_suite(
                owner=context.github_project.owner,
                repo=context.github_project.repository,
                check_suite_id=check_suite_id,
            )
        ).parsed_data
        for check_run in check_runs.check_runs:
            if check_run_id is None or check_run.id == check_run_id:
                _LOGGER.info(
                    "Re request the check run %s from check suite %s",
                    check_run.id,
                    check_suite.id,
                )
                outputs.append(f"Re request the check run {check_run.id} from check suite {check_suite.id}")
                number += 1
                await context.session.execute(
                    sqlalchemy.update(models.Queue)
                    .where(models.Queue.check_run_id == check_run.id)
                    .values(
                        {
                            "status": models.JobStatus.NEW.name,
                            "started_at": None,
                            "finished_at": None,
                        },
                    ),
                )
                await context.session.commit()
                await context.github_project.aio_github.rest.checks.async_update(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    check_run_id=check_run.id,
                    data={"status": "queued"},
                )
    except githubkit.exception.RequestFailed as exception:
        if exception.response.status_code == 404:
            _LOGGER.error(  # noqa: TRY400
                "Repository not found: %s/%s",
                context.github_project.owner,
                context.github_project.repository,
            )
            outputs.append(
                f"Repository not found: {context.github_project.owner}/{context.github_project.repository}",
            )
        else:
            _LOGGER.exception("Error while getting check suite")
            outputs.append(
                "Error while getting check suite",
            )
    return outputs, number


async def _process_event(context: module.ProcessContext[None, _EventData]) -> None:
    if "TEST_APPLICATION" in os.environ:
        job = models.Queue()
        job.priority = 0
        job.application = os.environ["TEST_APPLICATION"]
        job.owner = "camptocamp"
        job.repository = "test"
        job.event_name = "repo_event"
        job.event_data = context.event_data
        job.module = "dispatcher"
        job.module_data = context.module_event_data.model_dump()
        context.session.add(job)
    else:
        installations = (
            await context.github_project.application.aio_github.rest.apps.async_list_installations()
        ).parsed_data
        for installation in installations:
            aoi_installation_auth_strategy = context.github_project.application.aio_auth.as_installation(
                installation.id,
            )
            aio_github = context.github_project.application.aio_github.with_auth(
                aoi_installation_auth_strategy,
            )
            repos = (await aio_github.rest.apps.async_list_repos_accessible_to_installation()).parsed_data
            for repo in repos.repositories:
                job = models.Queue()
                job.priority = 0
                job.application = context.github_project.application.name
                job.owner = repo.owner.login
                job.repository = repo.name
                job.event_name = "repo_event"
                job.event_data = context.event_data
                job.module = "dispatcher"
                job.module_data = context.module_event_data.model_dump()
                context.session.add(job)
    await context.session.flush()


async def _process_repo_event(context: module.ProcessContext[None, _EventData]) -> None:
    _LOGGER.info(
        "Process the event: %s, application: %s",
        context.event_data.get("name"),
        os.environ["TEST_APPLICATION"]  # noqa: SIM401
        if "TEST_APPLICATION" in os.environ
        else context.github_project.application.name,
    )
    await process_event(context)
