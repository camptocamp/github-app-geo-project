# Copyright (c) 2026, Camptocamp SA

"""Application utility module."""

import asyncio
import datetime
import json
import logging
import urllib.parse
from collections.abc import Iterable, Mapping
from typing import Any

import githubkit.exception
import githubkit.webhooks
import githubkit_schemas.latest.models
import pygments.formatters
import pygments.lexers
import sqlalchemy.dialects.postgresql
import sqlalchemy.ext.asyncio
import yaml
from tinycss2 import parse_stylesheet, serialize

from github_app_geo_project import configuration, models, module

_LOGGER = logging.getLogger(__name__)

_ISSUE_START = "<!-- START {} -->"
_ISSUE_END = "<!-- END {} -->"

_JSON_LEXER = pygments.lexers.JsonLexer()  # pylint: disable=no-member
_YAML_LEXER = pygments.lexers.YamlLexer()  # pylint: disable=no-member
HTML_FORMATTER = pygments.formatters.HtmlFormatter(style="github-dark")  # pylint: disable=no-member


def get_dashboard_issue_module(text: str, current_module: str) -> str:
    """Get the part of the issue related to a module."""
    start_tag = _ISSUE_START.format(current_module)
    end_tag = _ISSUE_END.format(current_module)
    issue_data = ""
    if start_tag in text and end_tag in text:
        start = text.index(start_tag) + len(start_tag)
        end = text.index(end_tag)
        issue_data = text[start:end]
        issue_data = issue_data.strip()
        if issue_data.startswith("## "):
            issue_data = "\n".join(issue_data.split("\n")[1:]).strip()
    return issue_data


def update_dashboard_issue_module(
    text: str,
    module_name: str,
    current_module: module.Module[Any, Any, Any, Any],
    data: str,
) -> str:
    """Update the issue data (text) of a module with his new data."""
    start_tag = _ISSUE_START.format(module_name)
    end_tag = _ISSUE_END.format(module_name)
    issue_data = (
        "\n".join(
            [
                start_tag,
                f"## {current_module.title()}",
                "",
                data,
                end_tag,
            ],
        )
        if data
        else ""
    )
    if start_tag in text and end_tag in text:
        start = text.index(start_tag)
        end = text.index(end_tag) + len(end_tag)
        return text[:start] + issue_data + text[end:]
    return f"{text}{issue_data}"


def format_json(json_data: dict[str, Any]) -> str:
    """Format a JSON data to a HTML string."""
    return format_json_str(json.dumps(json_data, indent=4))


def format_json_str(json_str: str) -> str:
    """Format a JSON data to a HTML string."""
    return pygments.highlight(json_str, _JSON_LEXER, HTML_FORMATTER)  # type: ignore[no-any-return]


def format_yaml(yaml_data: dict[str, Any]) -> str:
    """Format a YAML data to a HTML string."""
    return pygments.highlight(  # type: ignore[no-any-return]
        yaml.dump(yaml_data, default_flow_style=False),
        _YAML_LEXER,
        HTML_FORMATTER,
    )


def datetime_with_timezone(date: datetime.datetime) -> datetime.datetime:
    """Add the timezone to a date."""
    if date.tzinfo:
        return date
    return date.replace(tzinfo=datetime.UTC)


def merge_css_blocks(css_blocks: Iterable[str]) -> str:
    """Merge the CSS rules without adding duplication."""
    merged_rules: dict[str, dict[str, str]] = {}

    for css in css_blocks:
        stylesheet = parse_stylesheet(css)
        for rule in stylesheet:
            if rule.type == "qualified-rule":
                selector = serialize(rule.prelude).strip()
                declarations: dict[str, str] = {}

                prop = ""
                value = ""
                for decl in rule.content:
                    if decl.type == "literal" and decl.value == ";":
                        if prop and value:
                            declarations[prop] = value
                        prop = ""
                        value = ""
                    if decl.type not in ("whitespace", "literal"):
                        if not prop:
                            prop = decl.serialize()
                        else:
                            value += decl.serialize()

                if prop and value:
                    declarations[prop] = value

                if selector not in merged_rules:
                    merged_rules[selector] = {}
                merged_rules[selector].update(declarations)

    merged_css = []
    for selector, props in merged_rules.items():
        flat_declarations = "; ".join(f"{prop}: {value}" for prop, value in props.items())
        merged_css.append(f"{selector} {{ {flat_declarations}; }}")

    return "\n".join(merged_css)


def normalize_push_event(event_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize push event data to match githubkit's WebhookPush schema.

    GitHub can send ``compare`` as ``null`` (e.g. on force-pushes) but the
    githubkit schema requires a string. This function returns a shallow copy
    with the offending fields coerced to their expected types.
    """
    normalized = event_data.copy()
    if "compare" in normalized and normalized["compare"] is None:
        normalized["compare"] = ""
    return normalized


def normalize_workflow_run_event(event_data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize workflow_run event data to match githubkit's schema.

    GitHub does not always send ``triggering_actor`` in workflow_run events,
    but the githubkit schema requires the field (it accepts ``null``). This
    function returns a copy with the missing field set to ``None``.
    """
    normalized: dict[str, Any] = dict(event_data)
    workflow_run = normalized.get("workflow_run")
    if isinstance(workflow_run, dict) and "triggering_actor" not in workflow_run:
        normalized["workflow_run"] = {**workflow_run, "triggering_actor": None}
    return normalized


_VALID_WORKFLOW_JOB_STEP_STATUSES = {"queued", "in_progress", "completed"}


def normalize_workflow_job_event(event_data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize workflow_job event data to match githubkit's schema.

    GitHub's webhook payload can include ``pending`` (or other values) as a
    step status, but the githubkit Pydantic model only accepts ``queued``,
    ``in_progress`` and ``completed``. This function returns a copy with any
    invalid step status rewritten to ``queued``.
    """
    normalized: dict[str, Any] = dict(event_data)
    workflow_job = normalized.get("workflow_job")
    if not isinstance(workflow_job, dict):
        return normalized
    steps = workflow_job.get("steps")
    if not isinstance(steps, list):
        return normalized
    new_steps: list[Any] = []
    for step in steps:
        if isinstance(step, dict) and step.get("status") not in _VALID_WORKFLOW_JOB_STEP_STATUSES:
            new_steps.append({**step, "status": "queued"})
        else:
            new_steps.append(step)
    normalized["workflow_job"] = {**workflow_job, "steps": new_steps}
    return normalized


def normalize_event(event_name: str, event_data: dict[str, Any]) -> dict[str, Any]:
    """Normalize GitHub webhook event data based on event name.

    Dispatches to the appropriate normalizer based on the event type.
    Returns the normalized data, or the original data if no normalization is needed.
    """
    if event_name == "push":
        return normalize_push_event(event_data)
    if event_name == "workflow_run":
        return normalize_workflow_run_event(event_data)
    if event_name == "workflow_job":
        return normalize_workflow_job_event(event_data)
    return event_data


async def create_checks(
    job: models.Queue,
    session: sqlalchemy.ext.asyncio.AsyncSession,
    current_module: module.Module[Any, Any, Any, Any],
    github_project: configuration.GithubProject,
    service_url: str,
) -> githubkit_schemas.latest.models.CheckRun | None:
    """Create the GitHub check run."""
    await session.flush()

    service_url = service_url if service_url.endswith("/") else service_url + "/"
    service_url = urllib.parse.urljoin(service_url, "logs/")
    service_url = urllib.parse.urljoin(service_url, str(job.id))

    sha = None
    if job.github_event_name == "pull_request":
        event_data_pull_request = githubkit.webhooks.parse_obj(
            "pull_request",
            job.github_event_data,
        )
        sha = event_data_pull_request.pull_request.head.sha
    if job.github_event_name == "push":
        event_data_push = githubkit.webhooks.parse_obj(
            "push",
            job.github_event_data,
        )
        sha = event_data_push.before if event_data_push.deleted else event_data_push.after
    if job.github_event_name == "workflow_run":
        event_data_workflow_run = githubkit.webhooks.parse_obj(
            "workflow_run",
            job.github_event_data,
        )
        sha = event_data_workflow_run.workflow_run.head_sha
    if job.github_event_name == "check_suite":
        event_data_check_suite = githubkit.webhooks.parse_obj(
            "check_suite",
            job.github_event_data,
        )
        sha = event_data_check_suite.check_suite.head_sha
    if job.github_event_name == "check_run":
        event_data_check_run = githubkit.webhooks.parse_obj(
            "check_run",
            job.github_event_data,
        )
        sha = event_data_check_run.check_run.head_sha
    if sha is None:
        branch = (
            await github_project.aio_github.rest.repos.async_get_branch(
                owner=github_project.owner,
                repo=github_project.repository,
                branch=await github_project.default_branch(),
            )
        ).parsed_data
        sha = branch.commit.sha
    if sha is None:
        message = f"No sha found for the job {job.id} in {job.github_event_name}"
        raise ValueError(message)

    name = f"{current_module.title()}: {job.github_event_name}"
    try:
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
    except githubkit.exception.RequestFailed as exception:
        _LOGGER.warning(
            "Failed to create check run for job %s: %s - %s\n%s",
            job.id,
            exception.response.status_code,
            exception.response.reason_phrase,
            exception.response.text,
        )
        return None
    job.check_run_id = check_run.id
    await session.commit()
    await session.refresh(job)
    return check_run


async def apply_jobs_unique_on(
    session: sqlalchemy.ext.asyncio.AsyncSession,
    current_module: module.Module[Any, Any, Any, Any],
    job: models.Queue,
    github_project: configuration.GithubProject | None = None,
) -> None:
    """
    Mark the existing new jobs that correspond to the given job as skipped.

    Based on the ``jobs_unique_on`` fields of the module, the new job replaces the previous one.
    Must be called before the job is added to the session.
    """
    jobs_unique_on = current_module.jobs_unique_on()
    if not jobs_unique_on:
        return

    conditions = [
        models.Queue.status == models.JobStatus.NEW.name,
        models.Queue.application == job.application,
        models.Queue.module == job.module,
    ]
    for key in jobs_unique_on:
        if key == module.Fields.PRIORITY:
            conditions.append(models.Queue.priority == job.priority)
        elif key == module.Fields.OWNER:
            conditions.append(models.Queue.owner == job.owner)
        elif key == module.Fields.REPOSITORY:
            conditions.append(models.Queue.repository == job.repository)
        elif key == module.Fields.GITHUB_EVENT_NAME:
            conditions.append(models.Queue.github_event_name == job.github_event_name)
        elif key == module.Fields.MODULE_EVENT_NAME:
            conditions.append(models.Queue.module_event_name == job.module_event_name)
        elif key == module.Fields.GITHUB_EVENT_DATA:
            conditions.append(
                sqlalchemy.cast(
                    models.Queue.github_event_data,
                    sqlalchemy.dialects.postgresql.JSONB,
                )
                == job.github_event_data,
            )
        elif key == module.Fields.MODULE_EVENT_DATA:
            conditions.append(
                sqlalchemy.cast(
                    models.Queue.module_event_data,
                    sqlalchemy.dialects.postgresql.JSONB,
                )
                == job.module_event_data,
            )
        else:
            _LOGGER.error("Unknown jobs_unique_on key: %s", key)

    result = await session.execute(
        sqlalchemy.update(models.Queue)
        .where(*conditions)
        .values(status=models.JobStatus.SKIPPED.name)
        .returning(models.Queue.id, models.Queue.check_run_id),
    )
    skipped_jobs = result.all()
    if not skipped_jobs:
        return
    _LOGGER.info(
        "%i job(s) skipped, replaced by the new job '%s' for module %s",
        len(skipped_jobs),
        job.module_event_name,
        job.module,
    )
    if (
        github_project is None
        or github_project.aio_github is None
        or job.owner is None
        or job.repository is None
    ):
        return
    aio_github = github_project.aio_github
    owner = job.owner
    repository = job.repository

    async def _skip_check_run(check_run_id: int) -> None:
        try:
            await aio_github.rest.checks.async_update(
                owner=owner,
                repo=repository,
                check_run_id=check_run_id,
                status="completed",
                conclusion="skipped",
            )
        except githubkit.exception.RequestFailed:
            # The job is already skipped, completing the check run is only cosmetic
            _LOGGER.warning(
                "Failed to mark the check run %s as skipped, the corresponding job is skipped",
                check_run_id,
            )

    async with asyncio.TaskGroup() as task_group:
        for _, check_run_id in skipped_jobs:
            if check_run_id is not None:
                task_group.create_task(_skip_check_run(check_run_id))
