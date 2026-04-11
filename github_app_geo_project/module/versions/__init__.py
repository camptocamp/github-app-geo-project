"""Utility functions for the auto* modules."""

import asyncio
import base64
import datetime
import io
import json
import logging
import os
import re
import tempfile
import tomllib
from collections.abc import Iterable
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any

import aiohttp
import anyio
import c2cciutils.configuration
import githubkit.exception
import githubkit.versions.latest.models
import security_md
import yaml
from pydantic import BaseModel, Field, model_serializer, model_validator

from github_app_geo_project import module, utils
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.versions import configuration

_LOGGER = logging.getLogger(__name__)

_UNSUPPORTED_COLOR = "--bs-danger"
_SUPPORTED_COLOR = "--bs-body-bg"


class _SupportCategory(IntEnum):
    NO_SUPPORT_DEFINED = 0
    UNSUPPORTED = 1
    BEST_EFFORT = 2
    TO_BE_DEFINED = 3
    DATE = 4
    UNKNOWN = -1


class _SupportType(Enum):
    NO_SUPPORT_DEFINED = "No support defined"
    UNSUPPORTED = "Unsupported"
    BEST_EFFORT = "Best effort"
    TO_BE_DEFINED = "To be defined"
    DATE = "Date"
    UNKNOWN = "Unknown"


def _support_display(support: "_Support") -> str:
    if support.type == _SupportType.DATE and support.until is not None:
        return support.until.strftime("%d/%m/%Y")
    return support.type.value


class _TransversalStatusNameByDatasource(BaseModel):
    names: list[str] = []


class _TransversalStatusVersions(BaseModel):
    versions: list[str] = []


class _TransversalStatusNameInDatasource(BaseModel):
    versions_by_names: dict[str, _TransversalStatusVersions] = {}


class _TransversalStatusDependencyBranches(BaseModel):
    branches: list[str] = []


class _TransversalStatusDependenciesByVersion(BaseModel):
    branches_by_version: dict[str, _TransversalStatusDependencyBranches] = {}


class _TransversalStatusDependenciesByDatasource(BaseModel):
    by_dependency: dict[str, _TransversalStatusDependenciesByVersion] = {}


class _TransversalStatusDependenciesIndex(BaseModel):
    by_datasource: dict[str, _TransversalStatusDependenciesByDatasource] = {}


class _TransversalStatusNamesByVersion(BaseModel):
    """Map canonical version -> support status for a package."""

    by_version: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _from_flat(cls, data: Any) -> Any:
        if isinstance(data, dict) and "by_version" not in data:
            return {"by_version": data}
        return data

    @model_serializer(mode="plain")
    def _to_flat(self) -> dict[str, str]:
        return self.by_version


class _TransversalStatusNamesByPackage(BaseModel):
    """Map package name -> versions map for one datasource."""

    by_package: dict[str, _TransversalStatusNamesByVersion] = {}

    @model_validator(mode="before")
    @classmethod
    def _from_flat(cls, data: Any) -> Any:
        if isinstance(data, dict) and "by_package" not in data:
            return {"by_package": data}
        return data

    @model_serializer(mode="plain")
    def _to_flat(self) -> dict[str, _TransversalStatusNamesByVersion]:
        return self.by_package


class _TransversalStatusNamesIndex(BaseModel):
    """Map datasource -> package map."""

    by_datasource: dict[str, _TransversalStatusNamesByPackage] = {}

    @model_validator(mode="before")
    @classmethod
    def _from_flat(cls, data: Any) -> Any:
        if isinstance(data, dict) and "by_datasource" not in data:
            return {"by_datasource": data}
        return data

    @model_serializer(mode="plain")
    def _to_flat(self) -> dict[str, _TransversalStatusNamesByPackage]:
        return self.by_datasource


class _Support(BaseModel):
    type: _SupportType = _SupportType.NO_SUPPORT_DEFINED
    until: datetime.date | None = None

    @model_serializer(mode="plain")
    def _to_plain(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type.value}
        if self.until is not None:
            data["until"] = self.until
        return data

    @model_validator(mode="before")
    @classmethod
    def _from_str(cls, data: Any) -> Any:
        if isinstance(data, str):
            parsed_date = _parse_support_date_value(data)
            if parsed_date is not None:
                return {"type": _SupportType.DATE, "until": parsed_date}
            try:
                return {"type": _SupportType(data)}
            except ValueError:
                return {"type": _SupportType.UNKNOWN}
        if isinstance(data, dict) and "type" in data:
            support_type = data.get("type")
            if isinstance(support_type, str):
                parsed_date = _parse_support_date_value(support_type)
                if parsed_date is not None:
                    data = {
                        **data,
                        "type": _SupportType.DATE,
                        "until": data.get("until") or parsed_date,
                    }
                else:
                    try:
                        data = {**data, "type": _SupportType(support_type)}
                    except ValueError:
                        data = {**data, "type": _SupportType.UNKNOWN}
        return data

    @model_validator(mode="after")
    def _set_until_from_type(self) -> "_Support":
        if self.until is None and self.type != _SupportType.UNKNOWN:
            self.until = _parse_support_date_value(str(self.type.value))
        return self


class _TransversalStatusVersion(BaseModel):
    support: _Support = Field(default_factory=_Support)
    names_by_datasource: dict[str, _TransversalStatusNameByDatasource] = {}
    dependencies_by_datasource: dict[str, _TransversalStatusNameInDatasource] = {}


class _TransversalStatusRepo(BaseModel):
    """Transversal dashboard repo."""

    versions: dict[str, _TransversalStatusVersion] = {}
    upstream_updated: datetime.datetime | None = None
    url: str | None = None
    has_security_policy: bool = False
    names_index: _TransversalStatusNamesIndex = Field(
        default_factory=_TransversalStatusNamesIndex,
    )
    """Pre-computed index for this repo: datasource -> package_name -> canonical_version -> support_status"""
    dependencies_index: _TransversalStatusDependenciesIndex = Field(
        default_factory=_TransversalStatusDependenciesIndex,
    )
    """Pre-computed index for this repo: datasource -> dependency_name[:dependency_version] -> normalized_version -> branches"""


class _NamesStatus(BaseModel):
    status_by_version: dict[str, str] = {}
    repo: str | None = None
    has_security_policy: bool = False


class _NamesByDataSources(BaseModel):
    by_package: dict[str, _NamesStatus] = {}


class _Names(BaseModel):
    by_datasources: dict[str, _NamesByDataSources] = {}


class _TransversalStatus(BaseModel):
    updated: dict[str, datetime.datetime] = {}
    """Repository updated time"""
    repositories: dict[str, _TransversalStatusRepo] = {}


class _IntermediateStatus(BaseModel):
    """The intermediate status."""

    step: int = 0
    version: str | None = None
    url: str | None = None
    has_security_policy: bool = False
    version_support: dict[str, str] = {}
    version_names_by_datasource: dict[str, _TransversalStatusNameByDatasource] = {}
    version_dependencies_by_datasource: dict[
        str,
        _TransversalStatusNameInDatasource,
    ] = {}
    stabilization_versions: list[str] = []
    external_repositories: dict[str, _TransversalStatusRepo] = {}


class _DependencyBase(BaseModel):
    name: str
    version: str
    support: str
    color: str
    repo: str

    @model_validator(mode="before")
    @classmethod
    def _normalize_support(cls, data: Any) -> Any:
        if isinstance(data, dict) and "support" in data:
            value = data.get("support")
            if isinstance(value, _Support):
                data = {**data, "support": _support_display(value)}
            elif isinstance(value, dict):
                data = {**data, "support": _support_display(_Support(**value))}
        return data


class _Dependency(_DependencyBase):
    datasource: str


class _DependencyReverse(_DependencyBase):
    pass


class _Dependencies(BaseModel):
    support: _Support = Field(default_factory=lambda: _Support(type=_SupportType.UNSUPPORTED))
    color: str = _UNSUPPORTED_COLOR
    forward: list[_Dependency] = []
    reverse: list[_DependencyReverse] = []


class _DependenciesBranches(BaseModel):
    by_branch: dict[str, _Dependencies] = {}


class _EventData(BaseModel):
    step: int
    version: str | None = None
    alternate_versions: list[str] | None = None
    retry: int | None = None
    previous_jobs: list[int] | None = None


class VersionError(Exception):
    """Error while updating the versions."""


class Versions(
    module.Module[
        configuration.VersionsConfiguration,
        _EventData,
        _TransversalStatus,
        _IntermediateStatus,
    ],
):
    """
    The version module.

    Create a dashboard to show the back ref versions with support check
    """

    def title(self) -> str:
        """Get the title of the module."""
        return "Versions"

    def description(self) -> str:
        """Get the description of the module."""
        return "Version back ref dashboard"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/module/versions/README.md"

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if (
            context.github_event_data.get("type") == "event"
            and context.github_event_data.get("name") == "versions-cron"
        ):
            return [
                module.Action(data=_EventData(step=1), priority=module.PRIORITY_CRON),
            ]
        return []

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        with (Path(__file__).parent / "schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            permissions={
                "contents": "read",
                "issues": "read",
                "metadata": "read",
            },
            events=set(),
        )

    async def process(
        self,
        context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    ) -> module.ProcessOutput[_EventData, _IntermediateStatus]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        intermediate_status = _IntermediateStatus(step=context.module_event_data.step)
        if context.module_event_data.step == 1:
            intermediate_status.url = (
                f"https://github.com/{context.github_project.owner}/{context.github_project.repository}"
            )

            if (
                context.module_config.get(
                    "repository-external",
                    "github-app-geo-project",
                )
                == context.github_project.repository
            ):
                await _update_upstream_versions(context, intermediate_status)

            stabilization_versions = []
            security_file_content = None
            security = None

            default_branch = await context.github_project.default_branch()

            try:
                security_file_content = (
                    await context.github_project.aio_github.rest.repos.async_get_content(
                        owner=context.github_project.owner,
                        repo=context.github_project.repository,
                        path="SECURITY.md",
                    )
                ).parsed_data
            except githubkit.exception.RequestFailed as exception:
                if exception.response.status_code != 404:
                    raise
            if security_file_content is not None:
                assert isinstance(
                    security_file_content,
                    githubkit.versions.latest.models.ContentFile,
                )
                security = security_md.Security(
                    base64.b64decode(security_file_content.content).decode("utf-8"),
                )

                stabilization_versions = security.branches()
                intermediate_status.has_security_policy = True
            else:
                _LOGGER.debug(
                    "No SECURITY.md file in the repository, apply only on default branch",
                )

            stabilization_versions.append(default_branch)
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_versions))
            for version in stabilization_versions:
                intermediate_status.version_support[version] = _SupportType.BEST_EFFORT.value

            if security is not None:
                version_index = security.version_index
                support_index = security.support_until_index
                for raw in security.data:
                    if len(raw) > max(version_index, support_index):
                        version = raw[version_index]
                        support = raw[support_index]
                        if version in intermediate_status.version_support:
                            intermediate_status.version_support[version] = support

            intermediate_status.stabilization_versions = stabilization_versions

            actions = [
                module.Action(
                    data=_EventData(
                        step=2,
                        version=version,
                        alternate_versions=(
                            module_utils.get_alternate_versions(security, version)
                            if security is not None
                            else []
                        ),
                        retry=int(
                            os.environ.get("GHCI_RENOVATE_GRAPH_RETRY_NUMBER", "10"),
                        ),
                        previous_jobs=[
                            *(context.module_event_data.previous_jobs or []),
                            context.job_id,
                        ],
                    ),
                    title=version,
                    priority=module.PRIORITY_CRON + 10,
                )
                for version in stabilization_versions
            ]
            return ProcessOutput(
                actions=actions,
                intermediate_status=intermediate_status,
                updated_transversal_status=True,
            )
        if context.module_event_data.step == 2:
            assert context.module_event_data.version is not None
            version = context.module_event_data.version
            intermediate_status.version = version
            branch = context.module_config.get("version-mapping", {}).get(
                version,
                version,
            )
            with tempfile.TemporaryDirectory() as tmpdirname:
                if os.environ.get("TEST") != "TRUE":
                    cwd = Path(tmpdirname)
                    new_cwd = await module_utils.git_clone(
                        context.github_project,
                        branch,
                        cwd,
                    )
                    if new_cwd is None:
                        exception_message = "Failed to clone the repository"
                        raise VersionError(exception_message)
                    cwd = new_cwd
                else:
                    cwd = Path.cwd()

                # Get Renovate configuration from master branch
                try:
                    renovate_file_content = (
                        await context.github_project.aio_github.rest.repos.async_get_content(
                            owner=context.github_project.owner,
                            repo=context.github_project.repository,
                            path=".github/renovate.json5",
                        )
                    ).parsed_data
                    assert isinstance(
                        renovate_file_content,
                        githubkit.versions.latest.models.ContentFile,
                    )

                    github_path = cwd / ".github"
                    github_path.mkdir(parents=True, exist_ok=True)
                    async with await anyio.open_file(
                        github_path / "renovate.json5",
                        "w",
                    ) as renovate_file:
                        await renovate_file.write(
                            base64.b64decode(renovate_file_content.content).decode(
                                "utf-8",
                            ),
                        )
                except githubkit.exception.RequestFailed as exception:
                    if exception.response.status_code != 404:
                        raise

                await _get_names(
                    context,
                    intermediate_status.version_names_by_datasource,
                    version,
                    cwd,
                    alternate_versions=context.module_event_data.alternate_versions,
                )
                message = module_utils.HtmlMessage(
                    utils.format_json(
                        json.loads(intermediate_status.model_dump_json())["version_names_by_datasource"],
                    ),
                )
                message.title = "Names:"
                _LOGGER.debug(message)
                if await _get_dependencies(
                    context,
                    intermediate_status.version_dependencies_by_datasource,
                    cwd,
                ):
                    assert context.module_event_data.retry is not None
                    # Retry
                    return ProcessOutput(
                        actions=[
                            module.Action(
                                data=_EventData(
                                    step=context.module_event_data.step,
                                    version=context.module_event_data.version,
                                    alternate_versions=context.module_event_data.alternate_versions,
                                    retry=context.module_event_data.retry - 1,
                                    previous_jobs=[
                                        *(context.module_event_data.previous_jobs or []),
                                        context.job_id,
                                    ],
                                ),
                            ),
                        ],
                    )
                message = module_utils.HtmlMessage(
                    utils.format_json(
                        json.loads(intermediate_status.model_dump_json())[
                            "version_dependencies_by_datasource"
                        ],
                    ),
                )
                message.title = "Dependencies:"
                _LOGGER.debug(message)

            return ProcessOutput(
                intermediate_status=intermediate_status,
                updated_transversal_status=True,
            )
        exception_message = "Invalid step"
        raise VersionError(exception_message)

    async def update_transversal_status(
        self,
        context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
        intermediate_status: _IntermediateStatus,
        transversal_status: _TransversalStatus,
    ) -> _TransversalStatus:
        """Update the transversal status with the intermediate status."""
        key = f"{context.github_project.owner}/{context.github_project.repository}"

        module_utils.manage_updated_separated(
            transversal_status.updated,
            transversal_status.repositories,
            key,
            days_old=10,
        )

        repo = transversal_status.repositories.setdefault(key, _TransversalStatusRepo())
        versions = repo.versions
        if intermediate_status.step == 1:
            _apply_additional_packages(context, transversal_status)

            if intermediate_status.url:
                repo.url = intermediate_status.url
                repo.has_security_policy = intermediate_status.has_security_policy

            for version_name in list(repo.versions):
                if version_name not in intermediate_status.version_support:
                    del repo.versions[version_name]

            for version_name, support in intermediate_status.version_support.items():
                version = repo.versions.setdefault(
                    version_name,
                    _TransversalStatusVersion(support=_Support(type=support)),
                )
                version.support = _Support(type=support)

            if intermediate_status.stabilization_versions:
                for version_name in list(versions.keys()):
                    if version_name not in intermediate_status.stabilization_versions:
                        del versions[version_name]

            for (
                external_name,
                external_repo,
            ) in intermediate_status.external_repositories.items():
                module_utils.manage_updated_separated(
                    transversal_status.updated,
                    transversal_status.repositories,
                    external_name,
                    days_old=10,
                )
                transversal_status.repositories[external_name] = external_repo

        if intermediate_status.step == 2:
            intermediate_version_name = intermediate_status.version
            assert intermediate_version_name is not None
            version = versions.setdefault(
                intermediate_version_name,
                _TransversalStatusVersion(
                    support=_Support(
                        type=(
                            _SupportType.BEST_EFFORT
                            if repo.has_security_policy
                            else _SupportType.NO_SUPPORT_DEFINED
                        ),
                    ),
                ),
            )
            if intermediate_status.version_names_by_datasource:
                version.names_by_datasource = intermediate_status.version_names_by_datasource
            if intermediate_status.version_dependencies_by_datasource:
                version.dependencies_by_datasource = intermediate_status.version_dependencies_by_datasource

            message = module_utils.HtmlMessage(
                utils.format_json_str(
                    version.model_dump_json(indent=2),
                ),
            )
            message.title = f"Version ({intermediate_status.version}):"
            _LOGGER.debug(message)

        message = module_utils.HtmlMessage(
            utils.format_json_str(
                repo.model_dump_json(indent=2),
            ),
        )
        message.title = "Repo:"
        _LOGGER.debug(message)

        _rebuild_repo_names(repo)
        _rebuild_repo_dependencies(repo)
        for external_name in intermediate_status.external_repositories:
            _rebuild_repo_names(transversal_status.repositories[external_name])
            _rebuild_repo_dependencies(transversal_status.repositories[external_name])

        return transversal_status

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self,
        context: module.TransversalDashboardContext[_TransversalStatus],
    ) -> module.TransversalDashboardOutput:
        """Get the dashboard data."""
        transversal_status = context.status

        if "repository" in context.params:
            names = _build_global_names(transversal_status)

            message = module_utils.HtmlMessage(
                utils.format_json_str(names.model_dump_json()),
            )
            message.title = "Names:"
            _LOGGER.debug(message)

            # branch = list of dependencies
            dependencies_branches = _DependenciesBranches()
            for version, version_data in transversal_status.repositories.get(
                context.params["repository"],
                _TransversalStatusRepo(),
            ).versions.items():
                _build_internal_dependencies(
                    version,
                    version_data,
                    names,
                    dependencies_branches,
                )
            _build_reverse_dependency(
                context.params["repository"],
                transversal_status.repositories.get(
                    context.params["repository"],
                    _TransversalStatusRepo(),
                ),
                transversal_status,
                dependencies_branches,
            )

            message = module_utils.HtmlMessage(
                utils.format_json_str(dependencies_branches.model_dump_json()),
            )
            message.title = "Dependencies branches:"
            _LOGGER.debug(message)

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "url": (
                        transversal_status.repositories[context.params["repository"]].url
                        if context.params["repository"] in transversal_status.repositories
                        else None
                    ),
                    "dependencies_branches": dependencies_branches,
                    "branches": _order_versions(dependencies_branches.by_branch.keys()),
                    "data": utils.format_json(
                        json.loads(
                            transversal_status.repositories.get(
                                context.params["repository"],
                                _TransversalStatusRepo(),
                            ).model_dump_json(),
                        ),
                    ),
                },
            )

        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/versions/dashboard.html",
            data={"repositories": list(transversal_status.repositories.keys())},
        )


_MINOR_VERSION_RE = re.compile(r"^(\d+\.\d+)(\..+)?$")


class _Version:
    """
    Helper class for comparing version strings.

    Provides custom comparisons for version strings, correctly handling
    numeric versions (integers like "20", "X.Y", or "X.Y.Z") versus
    non-numeric branch names (like "master", "main", "develop").

    Non-numeric versions sort after all numeric versions.
    """

    _VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?$")

    def __init__(self, version: str) -> None:
        """
        Initialize a version object.

        Arguments:
        ---------
            version: The version string to wrap
        """
        self.version = version

    def __cmp__(self, other: "_Version") -> int:
        r"""
        Compare this version with another.

        Numeric versions (matching `^\\d+(\\.(\\d+))?(\\.(\\d+))?(\\.\\d+)*$`,
        e.g. "20", "3.11", "1.2.3") are compared numerically by major,
        minor, then patch component; further components are ignored. Missing
        minor/patch components are treated as 0 (so "20", "20.0", and "20.0.0"
        are considered equal).
        Non-numeric versions (e.g. branch names like "master") sort after all
        numeric versions.

        Arguments:
        ---------
            other: The other version to compare with

        Returns
        -------
            0 if equal, positive if self > other, negative if self < other
        """
        if self.version == other.version:
            return 0
        match1 = self._VERSION_RE.match(self.version)
        match2 = self._VERSION_RE.match(other.version)
        if match1 is None and match2 is None:
            return 1 if self.version > other.version else -1
        if match1 is None:
            return 1
        if match2 is None:
            return -1
        tuple1 = (
            int(match1.group(1)),
            int(match1.group(2)) if match1.group(2) is not None else 0,
            int(match1.group(3)) if match1.group(3) is not None else 0,
        )
        tuple2 = (
            int(match2.group(1)),
            int(match2.group(2)) if match2.group(2) is not None else 0,
            int(match2.group(3)) if match2.group(3) is not None else 0,
        )
        if tuple1 == tuple2:
            return 0
        return 1 if tuple1 > tuple2 else -1

    def __lt__(self, other: "_Version") -> bool:
        """
        Less than comparison operator.

        Arguments:
        ---------
            other: The other version to compare with

        Returns
        -------
            True if self < other, False otherwise
        """
        return self.__cmp__(other) < 0


def _order_versions(versions: Iterable[str]) -> list[str]:
    """
    Sort a list of version strings in descending order.

    Uses the custom _Version class to handle semantic versioning comparisons correctly.

    Arguments:
    ---------
        versions: Iterable of version strings to sort

    Returns
    -------
        List of version strings sorted in descending order
    """
    return sorted(versions, reverse=True, key=_Version)


def _clean_version(version: str) -> str:
    """
    Clean a version string by removing common version prefixes.

    Arguments:
    ---------
        version: The version string to clean

    Returns
    -------
        The cleaned version string without prefixes like 'v' or '='
    """
    return version.lstrip("v=")


def _canonical_minor_version(datasource: str, version: str) -> str:
    """
    Convert a version string to its canonical minor version representation.

    For non-docker datasources, this extracts the major.minor part of the version,
    handling various version prefixes and formats.

    Arguments:
    ---------
        datasource: The type of datasource (e.g., 'docker', 'pypi', etc.)
        version: The version string to canonicalize

    Returns
    -------
        The canonical minor version representation
    """
    if datasource == "docker":
        return version

    if "," in version:
        versions = [v for v in version.split(",") if not v.startswith("<")]
        if len(versions) != 1:
            return version
        version = versions[0]

    version = _clean_version(version)
    version = version.lstrip("^>=")

    match = _MINOR_VERSION_RE.match(version)
    if match:
        return match.group(1)
    return version


def _rebuild_repo_names(repo_data: "_TransversalStatusRepo") -> None:
    """
    Rebuild the names index for a single repository.

    This pre-computes a flattened reverse index for the repo, mapping
    (datasource, package_name, canonical_version) to support status.
    The result is stored in repo_data.names_index and persisted with the repo.

    Arguments:
    ---------
        repo_data: The repository data to update in place
    """
    names_index = _TransversalStatusNamesIndex()
    for branch, branch_data in repo_data.versions.items():
        for datasource, datasource_data in branch_data.names_by_datasource.items():
            for name in datasource_data.names:
                names_index.by_datasource.setdefault(
                    datasource,
                    _TransversalStatusNamesByPackage(),
                ).by_package.setdefault(
                    name,
                    _TransversalStatusNamesByVersion(),
                ).by_version[_canonical_minor_version(datasource, branch)] = _support_display(
                    branch_data.support,
                )
    repo_data.names_index = names_index


def _rebuild_repo_dependencies(repo_data: "_TransversalStatusRepo") -> None:
    """Rebuild the per-repo dependency index used by reverse dependency lookups."""
    dependencies_index = _TransversalStatusDependenciesIndex()
    for branch, branch_data in repo_data.versions.items():
        for datasource, datasource_data in branch_data.dependencies_by_datasource.items():
            for dependency_name, dependency_versions in datasource_data.versions_by_names.items():
                for dependency_version in dependency_versions.versions:
                    indexed_name = (
                        f"{dependency_name}:{dependency_version}"
                        if datasource == "docker"
                        else dependency_name
                    )
                    normalized_version = _canonical_minor_version(datasource, dependency_version)
                    branches = (
                        dependencies_index.by_datasource.setdefault(
                            datasource,
                            _TransversalStatusDependenciesByDatasource(),
                        )
                        .by_dependency.setdefault(
                            indexed_name,
                            _TransversalStatusDependenciesByVersion(),
                        )
                        .branches_by_version.setdefault(
                            normalized_version,
                            _TransversalStatusDependencyBranches(),
                        )
                        .branches
                    )
                    if branch not in branches:
                        branches.append(branch)
    repo_data.dependencies_index = dependencies_index


def _build_global_names(transversal_status: "_TransversalStatus") -> _Names:
    """
    Assemble the global names lookup from the per-repo persisted indices.

    Merges all repos' pre-computed names_index into a single _Names structure
    used by _build_internal_dependencies. Falls back to building from versions
    data for repos that have no names_index yet (backward compatibility).

    Arguments:
    ---------
        transversal_status: The transversal status containing all repos

    Returns
    -------
        A _Names instance ready for dependency resolution
    """
    names = _Names()
    for repo_name, repo_data in transversal_status.repositories.items():
        for datasource, ds_packages in repo_data.names_index.by_datasource.items():
            ds_names = names.by_datasources.setdefault(datasource, _NamesByDataSources())
            for package_name, version_support in ds_packages.by_package.items():
                pkg_status = ds_names.by_package.setdefault(
                    package_name,
                    _NamesStatus(
                        repo=repo_name,
                        has_security_policy=repo_data.has_security_policy,
                    ),
                )
                pkg_status.status_by_version.update(version_support.by_version)
    return names


async def _get_names(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    names_by_datasource: dict[str, _TransversalStatusNameByDatasource],
    version: str,
    cwd: Path,
    alternate_versions: list[str] | None = None,
) -> None:
    """
    Extract package names from various project files.

    This function scans the repository for package definitions in different formats:
    - Python packages from pyproject.toml and setup.py
    - Docker images from GitHub publish configuration
    - NPM packages from package.json
    - GitHub releases

    Arguments:
    ---------
        context: The process context
        names_by_datasource: Output dictionary to store names by datasource
        version: The version (branch) being analyzed
        cwd: The current working directory (repository root)
        alternate_versions: Optional list of alternate versions to consider for Docker images
    """
    command = ["git", "ls-files", "pyproject.toml", "*/pyproject.toml"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage(
            command,
            proc.returncode,
            None if stdout is None else stdout.decode(),
            None if stderr is None else stderr.decode(),
        )
        message.title = "Unable to get the pyproject.toml files"
        _LOGGER.error(message)
    else:
        for filename in stdout.decode().splitlines():
            async with await anyio.open_file(cwd / filename, encoding="utf-8") as file:
                data = tomllib.loads(await file.read())
                name = data.get("project", {}).get("name")
                names = names_by_datasource.setdefault(
                    "pypi",
                    _TransversalStatusNameByDatasource(),
                ).names
                if name and name not in names:
                    names.append(name)
                else:
                    name = data.get("tool", {}).get("poetry", {}).get("name")
                    if name and name not in names:
                        names.append(name)
    command = ["git", "ls-files", "setup.py", "*/setup.py"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage(
            command,
            proc.returncode,
            None if stdout is None else stdout.decode(),
            None if stderr is None else stderr.decode(),
        )
        message.title = "Unable to get the setup.py files"
        _LOGGER.error(message)
    else:
        for filename in stdout.decode().splitlines():
            async with await anyio.open_file(cwd / filename, encoding="utf-8") as file:
                names = names_by_datasource.setdefault(
                    "pypi",
                    _TransversalStatusNameByDatasource(),
                ).names
                async for line in file:
                    match = re.match(r'^ *name ?= ?[\'"](.*)[\'"],?$', line)
                    if match and match.group(1) not in names:
                        names.append(match.group(1))
    os.environ["GITHUB_REPOSITORY"] = f"{context.github_project.owner}/{context.github_project.repository}"
    docker_config = {}
    if (cwd / ".github" / "publish.yaml").exists():
        async with await anyio.open_file(
            cwd / ".github" / "publish.yaml",
            encoding="utf-8",
        ) as file:
            docker_config = yaml.load(await file.read(), Loader=yaml.SafeLoader).get(
                "docker",
                {},
            )
    else:
        async with module_utils.WORKING_DIRECTORY_LOCK:
            os.chdir(cwd)
            data = c2cciutils.get_config()
            os.chdir("/")
        docker_config = data.get("publish", {}).get("docker", {})
    if docker_config:
        names = names_by_datasource.setdefault(
            "docker",
            _TransversalStatusNameByDatasource(),
        ).names
        all_versions = [version]
        if alternate_versions:
            all_versions.extend(alternate_versions)
        for conf in docker_config.get("images", []):
            for tag in conf.get("tags", ["{version}"]):
                for repository_conf in docker_config.get(
                    "repository",
                    c2cciutils.configuration.DOCKER_REPOSITORY_DEFAULT,
                ).values():
                    for ver in all_versions:
                        repository_host = repository_conf.get(
                            "host",
                            repository_conf.get("server", False),
                        )
                        add_names = []
                        if repository_host:
                            add_names.append(
                                f"{repository_host}/{conf.get('name')}:{tag.format(version=ver)}",
                            )

                        else:
                            add_names = [
                                f"{conf.get('name')}:{tag.format(version=ver)}",
                                f"docker.io/{conf.get('name')}:{tag.format(version=ver)}",
                            ]
                        for add_name in add_names:
                            if add_name not in names:
                                names.append(add_name)

    command = ["git", "ls-files", "package.json", "*/package.json"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(60):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage(
            command,
            proc.returncode,
            None if stdout is None else stdout.decode(),
            None if stderr is None else stderr.decode(),
        )
        message.title = "Unable to get the package.json files"
        _LOGGER.error(message)
    else:
        for filename in stdout.decode().splitlines():
            async with await anyio.open_file(cwd / filename, encoding="utf-8") as file:
                data = json.load(io.StringIO(await file.read()))
                name = data.get("name")
                names = names_by_datasource.setdefault(
                    "npm",
                    _TransversalStatusNameByDatasource(),
                ).names
                if name and name not in names:
                    names.append(name)

    names = names_by_datasource.setdefault(
        "github-release",
        _TransversalStatusNameByDatasource(),
    ).names
    add_name = f"{context.github_project.owner}/{context.github_project.repository}"
    if add_name not in names:
        names.append(add_name)


async def _get_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    result: dict[str, _TransversalStatusNameInDatasource],
    cwd: Path,
) -> bool:
    """
    Extract dependencies using renovate-graph.

    This function runs renovate-graph to analyze the repository dependencies,
    parsing its output to build a dependency graph.

    Arguments:
    ---------
        context: The process context
        result: Output dictionary to store dependency information
        cwd: The current working directory (repository root)

    Returns
    -------
        True if the operation should be retried, False otherwise
    """
    if os.environ.get("TEST") != "TRUE":
        github_project = context.github_project
        application = github_project.application
        username = application.slug + "[bot]"
        user = (await github_project.aio_github.rest.users.async_get_by_username(username)).parsed_data
        command = ["renovate-graph", "--platform=local"]
        proc = await asyncio.create_subprocess_exec(  # pylint: disable=subprocess-run-check
            *command,
            env={
                **os.environ,
                "RG_LOCAL_PLATFORM": "github",
                "RG_LOCAL_ORGANISATION": github_project.owner,
                "RG_LOCAL_REPO": github_project.repository,
                "RG_GITHUB_APP_ID": str(application.id),
                "RG_GITHUB_APP_KEY": application.private_key,
                "RENOVATE_USERNAME": username,
                "RENOVATE_GIT_AUTHOR": f"{username} <{user.id}+{username}@users.noreply.github.com>",
                "RG_GITHUB_APP_INSTALLATION_ID": str(user.id),
                "GITHUB_COM_TOKEN": github_project.token,
                "RENOVATE_REPOSITORIES": f"{github_project.owner}/{github_project.repository}",
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        async with asyncio.timeout(2700):  # 45 minutes
            stdout, stderr = await proc.communicate()
        message: module_utils.HtmlMessage = module_utils.AnsiProcessMessage.from_async_artifacts(
            command,
            proc,
            stdout,
            stderr,
        )
        if (
            proc.returncode != 0
            and context.module_event_data.retry is not None
            and context.module_event_data.retry > 0
        ):
            message.title = "Failed to get the dependencies (will retry)"
            _LOGGER.info(message)
            await asyncio.sleep(
                int(os.environ.get("GHCI_RENOVATE_GRAPH_RETRY_DELAY", "600")),
            )
            return True

        if proc.returncode != 0:
            message.title = "Failed to get the dependencies"
            _LOGGER.error(message)
            raise VersionError(message.title)
        message.title = "Got the dependencies"
        _LOGGER.debug(message)

        lines = stdout.decode().splitlines() if stdout else []
    else:
        lines = os.environ["RENOVATE_GRAPH"].splitlines()

    index = -1
    for i, line in enumerate(lines):
        if line == "DEBUG: packageFiles with updates":
            index = i + 1
            break
    if index != -1:
        lines = lines[index:]

    index = -1
    for i, line in enumerate(lines):
        if not line.startswith("       "):
            index = i
            break
    if index != -1:
        lines = lines[:index]

    json_str = "{\n" + "\n".join(lines) + "\n}"
    message = module_utils.HtmlMessage(utils.format_json_str(json_str))
    message.title = "Read dependencies from"
    _LOGGER.debug(message)
    data = json.loads(json_str)
    _read_dependencies(context, data, result)
    return False


def _read_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    data: dict[str, Any],
    result: dict[str, _TransversalStatusNameInDatasource],
) -> None:
    """
    Parse the dependency data from renovate-graph output.

    This function processes the raw dependency data, extracting and organizing
    dependencies according to their datasource and version.

    Arguments:
    ---------
        context: The process context
        data: The raw dependency data from renovate-graph
        result: Output dictionary to store processed dependency information
    """
    for values in data.get("config", {}).values():
        for value in values:
            for dep in value.get("deps", []):
                if "currentValue" not in dep:
                    continue
                if "datasource" not in dep:
                    continue
                for dependency, datasource, version in _dependency_extractor(
                    context,
                    dep["depName"],
                    dep["datasource"],
                    dep["currentValue"],
                ):
                    versions_by_names = result.setdefault(
                        datasource,
                        _TransversalStatusNameInDatasource(),
                    ).versions_by_names
                    versions = versions_by_names.setdefault(
                        dependency,
                        _TransversalStatusVersions(),
                    ).versions
                    if version not in versions:
                        versions.append(version)

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.versions_by_names.items():
            datasource_value.versions_by_names[dep] = dep_value


def _dependency_extractor(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    dependency: str,
    datasource: str,
    version: str,
) -> list[tuple[str, str, str]]:
    """
    Extract additional dependencies using configured extractors.

    This function applies configured package extractors to derive additional
    dependencies from existing ones, based on patterns in the version strings.

    Arguments:
    ---------
        context: The process context containing extractor configuration
        dependency: The original dependency name
        datasource: The datasource of the original dependency
        version: The version string of the original dependency

    Returns
    -------
        List of tuples (dependency, datasource, version) for all extracted dependencies
    """
    result = [(dependency, datasource, version)]

    for extractor_config in (
        context.module_config.get("package-extractor", {}).get(datasource, {}).get(dependency, [])
    ):
        extractor_re = re.compile(extractor_config.get("version-extractor", ""))
        match = extractor_re.match(version)
        if match:
            values = match.groupdict()
            do_extraction = True
            for key in extractor_config.get("requires", []):
                if key not in values:
                    do_extraction = False
                    break
            if not do_extraction:
                continue
            new_datasource = extractor_config["datasource"]
            new_package = extractor_config["package"].format(**values)
            new_version = extractor_config["version"].format(**values)
            result.append((new_package, new_datasource, new_version))
    return result


async def _update_upstream_versions(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    intermediate_status: _IntermediateStatus,
) -> None:
    """
    Fetch version information from endoflife.date API.

    This function queries the endoflife.date API for version support information
    for packages configured as "external-packages", and updates the intermediate
    status with this information.

    Arguments:
    ---------
        context: The process context containing external packages configuration
        intermediate_status: The intermediate status to update with external package information
    """
    for external_config in context.module_config.get("external-packages", []):
        package = external_config["package"]
        name = f"endoflife.date/{package}"
        datasource = external_config["datasource"]

        package_status = _TransversalStatusRepo(
            has_security_policy=True,
        )
        intermediate_status.external_repositories[name] = package_status
        package_status.url = f"https://endoflife.date/{package}"

        if package_status.upstream_updated and (
            package_status.upstream_updated
            > datetime.datetime.now(datetime.UTC)
            - utils.parse_duration(
                os.environ.get("GHCI_EXTERNAL_PACKAGES_UPDATE_PERIOD", "30d"),
            )
        ):
            return
        package_status.upstream_updated = datetime.datetime.now(datetime.UTC)

        async with (
            aiohttp.ClientSession() as session,
            asyncio.timeout(120),
            session.get(f"https://endoflife.date/api/{package}.json") as response,
        ):
            if not response.ok:
                _LOGGER.error("Failed to get the data for %s", package)
                package_status.upstream_updated = None
                return
            cycles = await response.json()
        message = module_utils.HtmlMessage(utils.format_json(cycles))
        message.title = f"Cycles {package}:"
        _LOGGER.debug(message)
        for cycle in cycles:
            eol = cycle.get("eol")
            support_until = None
            if eol is False:
                eol = _SupportType.BEST_EFFORT.value
            else:
                if not isinstance(eol, str):
                    continue
                parsed_eol = utils.datetime_with_timezone(
                    datetime.datetime.fromisoformat(eol),
                )
                if parsed_eol < datetime.datetime.now(datetime.UTC):
                    continue
                support_until = parsed_eol.astimezone(datetime.UTC).date()
                eol = parsed_eol.astimezone(datetime.UTC).strftime("%d/%m/%Y")
            package_status.versions[cycle["cycle"]] = _TransversalStatusVersion(
                support=_Support(type=eol, until=support_until),
                names_by_datasource={
                    datasource: _TransversalStatusNameByDatasource(
                        names=[package],
                    ),
                },
            )


def _parse_support_date(text: str) -> datetime.datetime | None:
    # Try ISO and DD/MM/YYYY date formats
    try:
        date = datetime.datetime.fromisoformat(text)
        if date.tzinfo is None:
            date = date.replace(tzinfo=datetime.UTC)
    except Exception:  # noqa: BLE001,S110
        pass
    else:
        return date
    try:
        return datetime.datetime.strptime(text, "%d/%m/%Y").replace(tzinfo=datetime.UTC)
    except Exception:  # noqa: BLE001,S110
        pass
    return None


def _parse_support_date_value(text: str) -> datetime.date | None:
    parsed = _parse_support_date(text)
    if parsed is None:
        return None
    return parsed.astimezone(datetime.UTC).date()


def _support_category(s: str, support_until: datetime.date | None = None) -> _SupportCategory:
    s = (s or "").strip().lower()
    if s == _SupportType.NO_SUPPORT_DEFINED.value.lower():
        return _SupportCategory.NO_SUPPORT_DEFINED
    if s == _SupportType.UNSUPPORTED.value.lower():
        return _SupportCategory.UNSUPPORTED
    if s == _SupportType.BEST_EFFORT.value.lower():
        return _SupportCategory.BEST_EFFORT
    if s == _SupportType.TO_BE_DEFINED.value.lower():
        return _SupportCategory.TO_BE_DEFINED
    if support_until is not None or _parse_support_date(s) is not None:
        return _SupportCategory.DATE
    return _SupportCategory.UNKNOWN  # Any other string


def _support_cmp(
    a: str,
    b: str,
    a_support_until: datetime.date | None = None,
    b_support_until: datetime.date | None = None,
) -> int:
    """
    Compare two support status strings.

    Returns
    -------
        -1 if a < b (a is less supported than b)
         0 if equal
         1 if a > b (a is better supported than b)

    Order: other < unsupported < best effort < to be defined < date.
    "Other" corresponds to any unrecognized support string and is treated as least supported.
    Older dates are considered less supported.
    """

    # Normalize once and use consistently for category and date parsing
    a_norm = (a or "").strip()
    b_norm = (b or "").strip()

    cat_a = _support_category(a_norm, a_support_until)
    cat_b = _support_category(b_norm, b_support_until)
    if _SupportCategory.NO_SUPPORT_DEFINED in (cat_a, cat_b):
        # No support defined is considered equal to everything to never be in red
        return 0
    if cat_a != cat_b:
        return -1 if cat_a < cat_b else 1
    if cat_a == _SupportCategory.DATE and cat_b == _SupportCategory.DATE:
        # Both are dates, compare as dates (oldest = less support)
        try:
            da = a_support_until or _parse_support_date_value(a_norm)
            db = b_support_until or _parse_support_date_value(b_norm)
            if da is None or db is None:
                message = f"Failed to parse support dates for comparison: {a!r}, {b!r}"
                raise ValueError(message)  # noqa: TRY301
            if da < db:
                return -1
            if da > db:
                return 1
        except Exception:
            message = "Error parsing dates for support comparison: %s, %s"
            _LOGGER.exception(message, a, b)
            return 0
    return 0


def _is_supported(
    base: str,
    other: str,
    base_support_until: datetime.date | None = None,
    other_support_until: datetime.date | None = None,
) -> bool:
    """
    Determine if a version is supported based on two support status strings.

    Compares the support status of the base with another support status.
    Returns True if the base support level is compatible with the other.

    Arguments:
    ---------
        base: The base support status string (e.g., "Best effort", a date string, etc.)
        other: The other support status string to compare with

    Returns
    -------
        True if the other status is supported relative to the base, False otherwise
    """
    # Use the comparison: other is supported if it is at least as good as base
    return (
        _support_cmp(
            base,
            other,
            a_support_until=base_support_until,
            b_support_until=other_support_until,
        )
        <= 0
    )


def _build_internal_dependencies(
    version: str,
    version_data: _TransversalStatusVersion,
    names: _Names,
    dependencies_branches: _DependenciesBranches,
) -> None:
    """
    Build the forward dependencies for a specific version of a repository.

    This function analyzes the dependencies of a specific version and builds
    a structured representation of them, including their support status.

    Arguments:
    ---------
        version: The version (branch) being analyzed
        version_data: The data for the version being analyzed
        names: Mapping of names across different datasources
        dependencies_branches: Output object to store the dependency information
    """
    dependencies_branch = dependencies_branches.by_branch.setdefault(
        version,
        _Dependencies(support=version_data.support, color=_SUPPORTED_COLOR),
    )
    for (
        datasource_name,
        dependencies_data,
    ) in version_data.dependencies_by_datasource.items():
        for (
            dependency_name,
            dependency_versions,
        ) in dependencies_data.versions_by_names.items():
            if datasource_name not in names.by_datasources:
                continue
            for dependency_version in dependency_versions.versions:
                dependency_data = names.by_datasources[datasource_name]
                if datasource_name == "docker":
                    full_dependency_name = f"{dependency_name}:{dependency_version}"
                else:
                    full_dependency_name = dependency_name
                # Ignore not owned dependencies
                if full_dependency_name not in dependency_data.by_package:
                    continue
                dependency_package_data = dependency_data.by_package[full_dependency_name]
                dependency_minor = _canonical_minor_version(
                    datasource_name,
                    dependency_version,
                )
                if datasource_name == "docker":
                    status_values = set(dependency_package_data.status_by_version.values())
                    if not status_values:
                        support = _SupportType.NO_SUPPORT_DEFINED.value
                    else:
                        real_statuses = {
                            s for s in status_values if s != _SupportType.NO_SUPPORT_DEFINED.value
                        }
                        candidates = list(real_statuses or status_values)
                        support = candidates[0]
                        for other_support in candidates[1:]:
                            if _support_cmp(other_support, support) < 0:
                                support = other_support
                elif not dependency_package_data.has_security_policy:
                    support = dependency_package_data.status_by_version.get(
                        dependency_minor,
                        _SupportType.NO_SUPPORT_DEFINED.value,
                    )
                else:
                    support = dependency_package_data.status_by_version.get(
                        dependency_minor,
                        _SupportType.UNSUPPORTED.value,
                    )
                clean_dependency_version = _clean_version(dependency_version)
                dependencies_branch.forward.append(
                    _Dependency(
                        name=dependency_name,
                        datasource=datasource_name,
                        version=(
                            dependency_minor
                            if dependency_minor == clean_dependency_version
                            else f"{dependency_minor} ({clean_dependency_version})"
                        ),
                        support=support,
                        color=(
                            _SUPPORTED_COLOR
                            if dependency_package_data.has_security_policy is False
                            or _is_supported(
                                version_data.support.type.value,
                                support,
                                base_support_until=version_data.support.until,
                            )
                            else _UNSUPPORTED_COLOR
                        ),
                        repo=dependency_package_data.repo or "-",
                    ),
                )


def _build_reverse_dependency(
    repository: str,
    repo_data: _TransversalStatusRepo,
    transversal_status: _TransversalStatus,
    dependencies_branches: _DependenciesBranches,
) -> None:
    """
    Build the reverse dependencies for a repository.

    This function identifies other repositories that depend on the current repository
    and builds a structured representation of these reverse dependencies,
    including their support status.

    Arguments:
    ---------
        repository: The name of the repository being analyzed
        repo_data: The data for the repository being analyzed
        transversal_status: The global status data containing all repositories
        dependencies_branches: Output object to store the dependency information
    """
    # Map of datasource names to packages to branches
    all_datasource_names: dict[str, dict[str, str]] = {}
    for branch, version_name_data in repo_data.versions.items():
        for (
            datasource_name,
            datasource_name_data,
        ) in version_name_data.names_by_datasource.items():
            for package_name in datasource_name_data.names:
                all_datasource_names.setdefault(datasource_name, {})[package_name] = branch
    for other_repo, other_repo_data in transversal_status.repositories.items():
        if repository == other_repo:
            continue

        for datasource_name, datasource_data in other_repo_data.dependencies_index.by_datasource.items():
            if datasource_name not in all_datasource_names:
                continue
            for package_name, versions_data in datasource_data.by_dependency.items():
                if package_name not in all_datasource_names[datasource_name]:
                    continue
                for minor_version, dependant_branches in versions_data.branches_by_version.items():
                    target_version = (
                        all_datasource_names[datasource_name][package_name]
                        if datasource_name == "docker"
                        else minor_version
                    )
                    for other_version in dependant_branches.branches:
                        other_version_data = other_repo_data.versions.get(other_version)
                        if other_version_data is None:
                            continue

                        version_data = repo_data.versions.get(target_version)
                        match = False
                        if version_data is not None and datasource_name in version_data.names_by_datasource:
                            match = package_name in version_data.names_by_datasource[datasource_name].names
                        if version_data is not None and match:
                            dependencies_branches.by_branch.setdefault(
                                target_version,
                                _Dependencies(color=_UNSUPPORTED_COLOR),
                            ).reverse.append(
                                _DependencyReverse(
                                    name=other_repo,
                                    version=_clean_version(other_version),
                                    support=_support_display(other_version_data.support),
                                    color=(
                                        _SUPPORTED_COLOR
                                        if _is_supported(
                                            other_version_data.support.type.value,
                                            version_data.support.type.value,
                                            base_support_until=other_version_data.support.until,
                                            other_support_until=version_data.support.until,
                                        )
                                        else _UNSUPPORTED_COLOR
                                    ),
                                    repo=other_repo,
                                ),
                            )
                        else:
                            dependencies_branches.by_branch.setdefault(
                                target_version,
                                _Dependencies(
                                    support=_Support(
                                        type=(
                                            _SupportType.UNSUPPORTED
                                            if repo_data.has_security_policy
                                            else _SupportType.NO_SUPPORT_DEFINED
                                        ),
                                    ),
                                    color=(
                                        _UNSUPPORTED_COLOR
                                        if repo_data.has_security_policy
                                        else _SUPPORTED_COLOR
                                    ),
                                ),
                            ).reverse.append(
                                _DependencyReverse(
                                    name=other_repo,
                                    version=_clean_version(other_version),
                                    support=_support_display(other_version_data.support),
                                    color=(
                                        _SUPPORTED_COLOR
                                        if not repo_data.has_security_policy
                                        else _UNSUPPORTED_COLOR
                                    ),
                                    repo=other_repo,
                                ),
                            )


def _apply_additional_packages(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData],
    transversal_status: _TransversalStatus,
) -> None:
    """
    Add additional packages specified in the module configuration to the transversal status.

    This function reads the 'additional-packages' section from the module configuration
    and adds these packages to the transversal status, allowing for manually specified
    packages to be included in the dashboard.

    Arguments:
    ---------
        context: The process context containing the module configuration
        transversal_status: The global status to update with additional packages
    """
    # Build a one-time index for O(1) dependency-support lookups:
    # (datasource_name, package_name, normalized_version) -> list[support]
    # The normalized version is computed per datasource (docker uses the full version,
    # others use the canonical major.minor form).
    support_index: dict[tuple[str, str, str], list[str]] = {}
    for other_repo_data in transversal_status.repositories.values():
        for other_version, other_version_data in other_repo_data.versions.items():
            cleaned_other_version = _clean_version(other_version)
            for other_datasource_name, other_name in other_version_data.names_by_datasource.items():
                normalized_other_version = _canonical_minor_version(
                    other_datasource_name, cleaned_other_version
                )
                for name in other_name.names:
                    support_index.setdefault(
                        (other_datasource_name, name, normalized_other_version), []
                    ).append(_support_display(other_version_data.support))

    for repo, data in context.module_config.get("additional-packages", {}).items():
        module_utils.manage_updated_separated(
            transversal_status.updated,
            transversal_status.repositories,
            repo,
            days_old=10,
        )
        if not isinstance(data, dict):
            continue
        versions = data.get("versions")
        if isinstance(versions, dict):
            for version_data in versions.values():
                if not isinstance(version_data, dict) or "support" in version_data:
                    continue
                deps_by_datasource = version_data.get("dependencies_by_datasource")
                if not isinstance(deps_by_datasource, dict):
                    version_data["support"] = _SupportType.NO_SUPPORT_DEFINED.value
                    continue
                # Gather all dependency supports using the pre-built index
                supports = []
                for dep_datasource_name, dep_datasource in deps_by_datasource.items():
                    if not isinstance(dep_datasource, dict):
                        continue
                    for dep_name, dep_versions in dep_datasource.get("versions_by_names", {}).items():
                        if not isinstance(dep_versions, dict):
                            continue
                        for dep_version in dep_versions.get("versions", []):
                            # Normalize the dependency version (datasource-aware) to align with lookups
                            normalized_dep_version = _canonical_minor_version(
                                dep_datasource_name,
                                _clean_version(dep_version),
                            )
                            # Build possible dependency name representations (e.g., for docker name:tag)
                            dep_name_candidates = {dep_name}
                            if dep_version:
                                dep_name_candidates.add(f"{dep_name}:{dep_version}")
                            if normalized_dep_version and normalized_dep_version != dep_version:
                                dep_name_candidates.add(f"{dep_name}:{normalized_dep_version}")
                            # Use support_index for O(1) lookup
                            for candidate in dep_name_candidates:
                                supports.extend(
                                    support_index.get(
                                        (dep_datasource_name, candidate, normalized_dep_version), []
                                    )
                                )
                if supports:
                    # Choose the "least" support (most restrictive)
                    min_support = supports[0]
                    for s in supports[1:]:
                        if _support_cmp(s, min_support) < 0:
                            min_support = s
                    version_data["support"] = min_support
                else:
                    version_data["support"] = _SupportType.UNSUPPORTED.value

        if isinstance(data, dict):
            versions = data.get("versions")
            if isinstance(versions, dict):
                for version_data in versions.values():
                    if isinstance(version_data, dict) and "support" not in version_data:
                        version_data["support"] = _SupportType.NO_SUPPORT_DEFINED.value

        pydentic_data = _TransversalStatusRepo(**data)
        _rebuild_repo_names(pydentic_data)
        _rebuild_repo_dependencies(pydentic_data)
        transversal_status.repositories[repo] = pydentic_data
