"""Module to dispatch publishing event."""

import logging
import urllib.parse
from typing import Any

import githubkit.versions.latest.models
import githubkit.webhooks
import sqlalchemy
from sqlalchemy.orm import Session

from github_app_geo_project import configuration, models, module
from github_app_geo_project.module import modules

_LOGGER = logging.getLogger(__name__)


class Webhook(module.Module[None, dict[str, Any], None, None]):
    """
    The version module.

    Create a dashboard to show the back ref versions with support check
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

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the actions of the module.

        Not needed for this module
        """
        del context
        return []

    async def process(
        self,
        context: module.ProcessContext[None, dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], None]:
        """
        Process the action.
        """

        re_requested_check_suite_id = _get_re_requested_check_suite_id(
            context.event_name,
            context.event_data,
        )
        outputs = (
            await _re_requested_check_suite(context, re_requested_check_suite_id)
            if re_requested_check_suite_id is not None
            else []
        )
        if not outputs:
            outputs.append("")

        process_event_outputs = await process_event(context)
        if not process_event_outputs:
            process_event_outputs.append("No action to process")
        return module.ProcessOutput(
            output={"summary": "\n".join([*outputs, *process_event_outputs])},
        )

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False


async def process_event(context: module.ProcessContext[None, dict[str, Any]]) -> list[str]:
    """Process the event."""
    _LOGGER.debug("Processing event for application %s", context.github_project.application.name)
    outputs = []
    for name in context.module_event_data.get("modules", []):
        current_module = modules.MODULES.get(name)
        if current_module is None:
            _LOGGER.error("Unknown module %s", name)
            continue
        _LOGGER.info(
            "Getting actions for the repository: %s/%s, module: %s",
            context.github_project.owner,
            context.github_project.repository,
            name,
        )
        try:
            for action in current_module.get_actions(
                module.GetActionContext(
                    event_name=context.event_name,
                    event_data=context.event_data,
                    owner=context.github_project.owner,
                    repository=context.github_project.repository,
                    github_application=context.github_project.application,
                ),
            ):
                _LOGGER.info(
                    "Got action %s",
                    action.title or "Untitled",
                )
                outputs.append(f"Create job for module {name} with action {action.title or 'Untitled'}")
                priority = action.priority if action.priority >= 0 else module.PRIORITY_STANDARD
                event_name = action.title or context.event_name
                module_data = current_module.event_data_to_json(action.data)

                jobs_unique_on = current_module.jobs_unique_on()
                if jobs_unique_on:
                    update = (
                        sqlalchemy.update(models.Queue)
                        .where(models.Queue.status == models.JobStatus.NEW)
                        .where(models.Queue.application == context.github_project.application.name)
                        .where(models.Queue.module == name)
                    )
                    for key in jobs_unique_on:
                        if key == "priority":
                            update = update.where(models.Queue.priority == priority)
                        elif key == "owner":
                            update = update.where(models.Queue.owner == context.github_project.owner)
                        elif key == "repository":
                            update = update.where(
                                models.Queue.repository == context.github_project.repository,
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
                            "status": models.JobStatus.SKIPPED,
                        },
                    )

                    context.session.execute(update)

                job = models.Queue()
                job.priority = priority
                job.application = context.github_project.application.name
                job.owner = context.github_project.owner
                job.repository = context.github_project.repository
                job.event_name = event_name
                job.event_data = context.event_data
                job.module = name
                job.module_data = module_data
                context.session.add(job)
                context.session.flush()
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
                    await create_checks(
                        job,
                        context.session,
                        current_module,
                        github_project,
                        context.event_name,
                        context.event_data,
                        context.service_url,
                        action.title,
                    )
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Error while getting actions for %s", name)
    context.session.commit()
    return outputs


async def create_checks(
    job: models.Queue,
    session: Session,
    current_module: module.Module[Any, Any, Any, Any],
    github_project: configuration.GithubProject,
    event_name: str,
    event_data: dict[str, Any],
    service_url: str,
    sub_name: str | None = None,
) -> githubkit.versions.latest.models.CheckRun:
    """Create the GitHub check run."""
    # Get the job id from the database
    session.flush()

    service_url = service_url if service_url.endswith("/") else service_url + "/"
    service_url = urllib.parse.urljoin(service_url, "logs/")
    service_url = urllib.parse.urljoin(service_url, str(job.id))

    sha = None
    if event_name == "pull_request":
        event_data_pull_request = githubkit.webhooks.parse_obj("pull_request", event_data)
        sha = event_data_pull_request.pull_request.head.sha
    if event_name == "push":
        event_data_push = githubkit.webhooks.parse_obj("push", event_data)
        sha = event_data_push.before if event_data_push.deleted else event_data_push.after
    if event_name == "workflow_run":
        event_data_workflow_run = githubkit.webhooks.parse_obj("workflow_run", event_data)
        sha = event_data_workflow_run.workflow_run.head_sha
    if event_name == "check_suite":
        event_data_check_suite = githubkit.webhooks.parse_obj("check_suite", event_data)
        sha = event_data_check_suite.check_suite.head_sha
    if event_name == "check_run":
        event_data_check_run = githubkit.webhooks.parse_obj("check_run", event_data)
        sha = event_data_check_run.check_run.head_sha
    if sha is None:
        assert github_project.aio_repo is not None
        branch = (
            await github_project.aio_github.rest.repos.async_get_branch(
                owner=github_project.owner,
                repo=github_project.repository,
                branch=github_project.aio_repo.default_branch,
            )
        ).parsed_data
        sha = branch.commit.sha

    name = f"{current_module.title()}: {sub_name}" if sub_name else current_module.title()
    check_run = (
        await github_project.aio_github.rest.checks.async_create(
            owner=github_project.owner,
            repo=github_project.repository,
            name=name,
            head_sha=sha,
            details_url=service_url,
            external_id=str(job.id),
        )
    ).parsed_data
    job.check_run_id = check_run.id
    session.commit()
    return check_run


def _get_re_requested_check_suite_id(event_name: str, event_data: dict[str, Any]) -> int | None:
    """Check if the event is a rerequested event."""
    if event_name != "check_run":
        return None

    event_data_check_suite = githubkit.webhooks.parse_obj("check_run", event_data)
    if (
        event_data_check_suite.action == "rerequested"
        and event_data_check_suite.check_run
        and event_data_check_suite.check_run.check_suite
        and event_data_check_suite.check_run.check_suite.id
    ):
        return event_data_check_suite.check_run.check_suite.id
    return None


async def _re_requested_check_suite(
    context: module.ProcessContext[None, dict[str, Any]],
    check_suite_id: int,
) -> list[str]:
    outputs = []
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
            _LOGGER.info(
                "Re request the check run %s from check suite %s",
                check_run.id,
                check_suite.id,
            )
            outputs.append(f"Re request the check run {check_run.id} from check suite {check_suite.id}")
            context.session.execute(
                sqlalchemy.update(models.Queue)
                .where(models.Queue.check_run_id == check_run.id)
                .values(
                    {
                        "status": models.JobStatus.NEW,
                        "started_at": None,
                        "finished_at": None,
                    },
                ),
            )
            context.session.commit()
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
    return outputs
