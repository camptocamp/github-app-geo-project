"""Application settings loaded from environment variables."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from typing import Annotated, Any, cast

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOGGER = logging.getLogger(__name__)

_ISO_DURATION_RE = re.compile(
    r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$",
)


def _unit_to_name(unit: str) -> str:
    return {"w": "weeks", "d": "days", "h": "hours", "m": "minutes", "s": "seconds"}[unit]


def _next_unit(unit: str) -> str:
    order = ["w", "d", "h", "m", "s"]
    idx = order.index(unit)
    return _unit_to_name(order[idx + 1]) if idx + 1 < len(order) else "seconds"


def parse_duration(text: str | datetime.timedelta) -> datetime.timedelta:
    """
    Parse a duration string to a timedelta.

    Supports ISO 8601 duration format (e.g. PT3H, P30D, PT600S) and
    the short format (e.g. 2h30, 2m30, 1d, 1w, 1w2d3h4m5s).
    The supported units are: w (weeks), d (days), h (hours), m (minutes), s (seconds).
    When the last number has no unit, it takes the next logical unit
    (e.g. 2h30 = 2h30m, 2m30 = 2m30s).
    """
    if isinstance(text, datetime.timedelta):
        return text
    match = _ISO_DURATION_RE.match(text)
    if match:
        parts = match.groups()
        return datetime.timedelta(
            days=int(parts[2] or 0),
            hours=int(parts[3] or 0),
            minutes=int(parts[4] or 0),
            seconds=float(parts[5] or 0),
        )
    segments = re.findall(r"(\d+)([wdhms])?", text)
    if segments:
        kwargs: dict[str, int] = {}
        last_unit = "s"
        for value, unit in segments:
            if unit:
                kwargs.setdefault(_unit_to_name(unit), int(value))
                last_unit = unit
            else:
                kwargs.setdefault(_next_unit(last_unit), int(value))
        return datetime.timedelta(**kwargs)
    message = f"Invalid time delta: {text}"
    raise ValueError(message)


Duration = Annotated[datetime.timedelta, BeforeValidator(parse_duration)]


def _json_loads(value: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return cast("dict[str, Any]", json.loads(value or "{}"))


JsonDict = Annotated[dict[str, Any], BeforeValidator(_json_loads)]


class _AuditTimeouts(BaseModel):
    subprocess: Annotated[Duration, Field(description="General subprocess timeout")] = datetime.timedelta(
        minutes=1
    )
    pip_freeze: Annotated[Duration, Field(description="pip freeze timeout")] = datetime.timedelta(minutes=1)
    precommit: Annotated[Duration, Field(description="pre-commit timeout")] = datetime.timedelta(minutes=20)
    git_diff: Annotated[Duration, Field(description="git diff timeout")] = datetime.timedelta(minutes=1)
    gradle: Annotated[Duration, Field(description="Gradle timeout")] = datetime.timedelta(minutes=1)
    git_lsfiles: Annotated[Duration, Field(description="git ls-files timeout")] = datetime.timedelta(
        minutes=1
    )
    python_install: Annotated[Duration, Field(description="Python install timeout")] = datetime.timedelta(
        minutes=10
    )
    snyk: Annotated[Duration, Field(description="Snyk timeout")] = datetime.timedelta(minutes=5)
    snyk_fix: Annotated[Duration, Field(description="Snyk fix timeout")] = datetime.timedelta(minutes=10)
    poetry_version: Annotated[Duration, Field(description="Poetry version timeout")] = datetime.timedelta(
        seconds=10
    )
    npm_audit: Annotated[Duration, Field(description="npm audit timeout")] = datetime.timedelta(minutes=5)


class _ProcessQueueSettings(BaseModel):
    logs_stream_interval: Annotated[Duration, Field(description="Logs stream interval")] = datetime.timedelta(
        seconds=2
    )
    job_timeout: Annotated[Duration, Field(description="Job timeout")] = datetime.timedelta(minutes=50)
    job_timeout_error: Annotated[Duration, Field(description="Job timeout error threshold")] = (
        datetime.timedelta(days=1)
    )
    create_dashboard_issue: Annotated[bool, Field(description="Create dashboard issue flag")] = True
    empty_thread_sleep: Annotated[Duration, Field(description="Sleep when no jobs")] = datetime.timedelta(
        seconds=10
    )
    debug: Annotated[bool, Field(description="Debug mode")] = False
    max_workers: Annotated[int, Field(description="Max thread pool workers")] = 2
    slow_callback_duration: Annotated[Duration, Field(description="Slow callback duration")] = (
        datetime.timedelta(minutes=1)
    )
    priority_groups: Annotated[str, Field(description="Priority groups")] = "2147483647"
    socket_timeout: Annotated[Duration, Field(description="Socket timeout")] = datetime.timedelta(minutes=2)


class _C2cciutilsSettings(BaseModel):
    timeout: Annotated[datetime.timedelta, Field(description="c2cciutils timeout")] = datetime.timedelta(
        seconds=30
    )


class _SqlAlchemySettings(BaseModel):
    url: Annotated[str, Field(description="SQLAlchemy database URL")] = (
        f"postgresql://{os.environ.get('PGUSER', 'postgresql')}:{os.environ.get('PGPASSWORD', 'postgresql')}"
        f"@{os.environ.get('PGHOST', 'db')}:{os.environ.get('PGPORT', '5432')}"
        f"/{os.environ.get('PGDATABASE', 'tests')}"
    )
    pool_recycle: Annotated[int | None, Field(description="DB pool recycle seconds")] = None
    pool_size: Annotated[int | None, Field(description="DB pool size")] = None
    max_overflow: Annotated[int | None, Field(description="DB max overflow")] = None
    db_schema: Annotated[str, Field(description="DB schema name")] = "ghci"

    @property
    def async_url(self) -> str:
        return self.url.replace("postgresql://", "postgresql+asyncpg://", 1)


class _GitHubApp(BaseModel):
    id: Annotated[int, Field(description="GitHub app ID")]
    private_key: Annotated[str, Field(description="GitHub app private key")]
    url: Annotated[str | None, Field(description="GitHub app URL")] = None
    admin_url: Annotated[str | None, Field(description="GitHub app admin URL")] = None
    webhook_secret: Annotated[str | None, Field(description="GitHub app webhook secret")] = None

    @field_validator("private_key", mode="before")
    @classmethod
    def _normalize_private_key(cls, value: str) -> str:
        return "\n".join([e.strip() for e in value.strip().split("\n")])


class _AppConfig(BaseModel):
    github_app: Annotated[_GitHubApp, Field(description="GitHub app configuration")]
    title: Annotated[str | None, Field(description="Application title")] = None
    description: Annotated[str | None, Field(description="Application description")] = None
    modules: Annotated[list[str], Field(description="Space-separated module names")] = []


class _RedisSettings(BaseModel):
    host: Annotated[str | None, Field(description="Redis host")] = None
    port: Annotated[int, Field(description="Redis port")] = 6379
    db: Annotated[int, Field(description="Redis DB number")] = 0
    username: Annotated[str | None, Field(description="Redis username")] = None
    password: Annotated[str | None, Field(description="Redis password")] = None
    ssl: Annotated[bool, Field(description="Redis SSL flag")] = False


class _WebhookSettings(BaseModel):
    secret_dry_run: Annotated[bool, Field(description="Webhook dry run")] = False
    github_secret: Annotated[str | None, Field(description="GitHub webhook HMAC secret")] = None


class _DispatchPublishingSettings(BaseModel):
    config: Annotated[JsonDict, Field(description="Dispatch publish config")] = {}


class _VersionsSettings(BaseModel):
    renovate_graph_retry_number: Annotated[int, Field(description="Renovate retry number")] = 10
    renovate_graph_retry_delay: Annotated[Duration, Field(description="Renovate retry delay")] = (
        datetime.timedelta(minutes=10)
    )
    external_packages_update_period: Annotated[Duration, Field(description="Update period")] = (
        datetime.timedelta(days=30)
    )


class _AuditSettings(BaseModel):
    dpkg_cache_duration: Annotated[Duration, Field(description="DPKG cache duration")] = datetime.timedelta(
        hours=3
    )


class _TestSettings(BaseModel):
    """Test settings."""

    app_name: Annotated[str | None, Field(description="Test application name")] = None
    github_app_id: Annotated[str | None, Field(description="Test GitHub app ID")] = None
    github_app_private_key: Annotated[str | None, Field(description="Test GitHub app private key")] = None


class ApplicationSettings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_prefix="GHCI__",
        env_nested_delimiter="__",
    )

    template_dir: Annotated[str, Field(description="Templates directory path")] = (
        "/app/github_app_geo_project/templates"
    )
    service_url: Annotated[str, Field(description="Base URL")] = "http://localhost:8080/"
    session_secret: Annotated[str, Field(description="Session secret")] = "change-me"  # noqa: S105
    configuration: Annotated[str | None, Field(description="Config YAML path")] = None
    sqlalchemy: Annotated[_SqlAlchemySettings, Field(description="Database settings")] = _SqlAlchemySettings()
    test: Annotated[_TestSettings, Field(description="Test settings")] = _TestSettings()
    application_configs: Annotated[dict[str, _AppConfig], Field(description="Application configs")] = {}
    redis: Annotated[_RedisSettings, Field(description="Redis settings")] = _RedisSettings()
    webhook: Annotated[_WebhookSettings, Field(description="Webhook settings")] = _WebhookSettings()
    dispatch_publishing: Annotated[
        _DispatchPublishingSettings, Field(description="Dispatch publishing settings")
    ] = _DispatchPublishingSettings()
    versions: Annotated[_VersionsSettings, Field(description="Versions settings")] = _VersionsSettings()
    audit: Annotated[_AuditSettings, Field(description="Audit settings")] = _AuditSettings()
    audit_timeouts: Annotated[_AuditTimeouts, Field(description="Audit timeouts")] = _AuditTimeouts()
    process_queue: Annotated[_ProcessQueueSettings, Field(description="Process queue")] = (
        _ProcessQueueSettings()
    )
    c2cciutils: Annotated[_C2cciutilsSettings, Field(description="c2cciutils")] = _C2cciutilsSettings()

    @model_validator(mode="before")
    @classmethod
    def _parse_application_configs(cls, data: dict[str, Any]) -> dict[str, Any]:
        app_configs: dict[str, dict[str, Any]] = {}
        for key, value in os.environ.items():
            if key.startswith("GHCI__APPLICATION__") and key != "GHCI__APPLICATION__":
                suffix = key[len("GHCI__APPLICATION__") :]
                if "__" not in suffix:
                    continue
                app_name, prop_name = suffix.split("__", 1)
                prop_name = prop_name.lower()
                app_configs.setdefault(app_name.lower(), {})[prop_name] = value
        parsed: dict[str, _AppConfig] = {}
        for app_name, props in app_configs.items():
            try:
                parsed[app_name] = _AppConfig(
                    github_app=_GitHubApp(
                        id=int(props.get("github_app_id") or 0),
                        private_key=props.get("github_app_private_key", ""),
                        url=props.get("github_app_url"),
                        admin_url=props.get("github_app_admin_url"),
                        webhook_secret=props.get("github_app_webhook_secret"),
                    ),
                    title=props.get("title"),
                    description=props.get("description"),
                    modules=props["modules"].split()
                    if isinstance(props.get("modules"), str)
                    else props.get("modules", []),
                )
            except Exception:
                _LOGGER.exception("Failed to parse application config for %s", app_name)
        data["application_configs"] = parsed
        return data


settings = ApplicationSettings()
