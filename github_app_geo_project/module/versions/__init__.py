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
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
import c2cciutils.configuration
import githubkit.exception
import githubkit.versions.latest.models
import security_md
import yaml
from pydantic import BaseModel

from github_app_geo_project import module, utils
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.versions import configuration

_LOGGER = logging.getLogger(__name__)

_NO_SUPPORT_DEFINED = "No support defined"
_UNSUPPORTED = "Unsupported"
_UNSUPPORTED_COLOR = "--bs-danger"
_SUPPORTED_COLOR = "--bs-body-bg"


class _TransversalStatusNameByDatasource(BaseModel):
    names: list[str] = []


class _TransversalStatusVersions(BaseModel):
    versions: list[str] = []


class _TransversalStatusNameInDatasource(BaseModel):
    versions_by_names: dict[str, _TransversalStatusVersions] = {}


class _TransversalStatusVersion(BaseModel):
    support: str
    names_by_datasource: dict[str, _TransversalStatusNameByDatasource] = {}
    dependencies_by_datasource: dict[str, _TransversalStatusNameInDatasource] = {}


class _TransversalStatusRepo(BaseModel):
    """Transversal dashboard repo."""

    versions: dict[str, _TransversalStatusVersion] = {}
    upstream_updated: datetime.datetime | None = None
    url: str | None = None
    has_security_policy: bool = False


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


class _NamesStatus(BaseModel):
    status_by_version: dict[str, str] = {}
    repo: str | None = None
    has_security_policy: bool = False


class _NamesByDataSources(BaseModel):
    by_package: dict[str, _NamesStatus] = {}


class _Names(BaseModel):
    by_datasources: dict[str, _NamesByDataSources] = {}


class _Dependency(BaseModel):
    name: str
    datasource: str
    version: str
    support: str
    color: str
    repo: str


class _Dependencies(BaseModel):
    support: str = _UNSUPPORTED
    color: str = _UNSUPPORTED_COLOR
    forward: list[_Dependency] = []
    reverse: list[_Dependency] = []


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
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Versions"

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

                stabilization_versions = module_utils.get_stabilization_versions(
                    security,
                )
                intermediate_status.has_security_policy = True
            else:
                _LOGGER.debug(
                    "No SECURITY.md file in the repository, apply only on default branch",
                )

            stabilization_versions.append(default_branch)
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_versions))
            for version in stabilization_versions:
                intermediate_status.version_support[version] = "Best effort"

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
                            *(
                                context.module_event_data.previous_jobs
                                if context.module_event_data.previous_jobs
                                else []
                            ),
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
                    async with aiofiles.open(
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
                                        *(
                                            context.module_event_data.previous_jobs
                                            if context.module_event_data.previous_jobs
                                            else []
                                        ),
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

            for version_name in repo.versions:
                if version_name not in intermediate_status.version_support:
                    del repo.versions[version_name]

            for version_name, support in intermediate_status.version_support.items():
                version = repo.versions.setdefault(
                    version_name,
                    _TransversalStatusVersion(support=support),
                )
                version.support = support

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
                    support="Best effort",
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
            names = _Names()
            for repo, repo_data in transversal_status.repositories.items():
                for branch, branch_data in repo_data.versions.items():
                    for (
                        datasource,
                        datasource_data,
                    ) in branch_data.names_by_datasource.items():
                        for name in datasource_data.names:
                            current_status = names.by_datasources.setdefault(
                                datasource,
                                _NamesByDataSources(),
                            ).by_package.setdefault(
                                name,
                                _NamesStatus(
                                    repo=repo,
                                    has_security_policy=repo_data.has_security_policy,
                                ),
                            )
                            current_status.status_by_version[_canonical_minor_version(datasource, branch)] = (
                                branch_data.support
                            )

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
    semantic versioning with major and minor components.
    """

    _VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")

    def __init__(self, version: str) -> None:
        """
        Initialize a version object.

        Arguments:
        ---------
            version: The version string to wrap
        """
        self.version = version

    def __cmp__(self, other: "_Version") -> int:
        """
        Compare this version with another.

        Implements a custom comparison that handles versions in the format "X.Y"
        specially, comparing components numerically rather than lexicographically.

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
            return -1
        if match2 is None:
            return 1
        if match1.group(1) == match2.group(1):
            if match1.group(2) == match2.group(2):
                return 0
            return 1 if match1.group(2) > match2.group(2) else -1
        return 1 if match1.group(1) > match2.group(1) else -1

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
            async with aiofiles.open(cwd / filename, encoding="utf-8") as file:
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
            async with aiofiles.open(cwd / filename, encoding="utf-8") as file:
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
        async with aiofiles.open(
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
            async with aiofiles.open(cwd / filename, encoding="utf-8") as file:
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
            if eol is False:
                eol = "Best effort"
            else:
                if not isinstance(eol, str):
                    continue
                if utils.datetime_with_timezone(
                    datetime.datetime.fromisoformat(eol),
                ) < datetime.datetime.now(
                    datetime.UTC,
                ):
                    continue
            package_status.versions[cycle["cycle"]] = _TransversalStatusVersion(
                support=eol,
                names_by_datasource={
                    datasource: _TransversalStatusNameByDatasource(
                        names=[package],
                    ),
                },
            )


def _parse_support_date(text: str) -> datetime.datetime:
    """
    Parse a date string into a datetime object with timezone.

    Handles both ISO format and DD/MM/YYYY format.

    Arguments:
    ---------
        text: The date string to parse

    Returns
    -------
        A datetime object with UTC timezone
    """
    try:
        return utils.datetime_with_timezone(datetime.datetime.fromisoformat(text))
    except ValueError:
        # Parse date like 01/01/2024
        return datetime.datetime.strptime(text, "%d/%m/%Y").replace(
            tzinfo=datetime.UTC,
        )


def _is_supported(base: str, other: str) -> bool:
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
    base = base.lower()
    other = other.lower()
    if base == other:
        return True
    if base == "unsupported":
        return True
    if other == "unsupported":
        return False
    if base == "best effort":
        return True
    if other == "best effort":
        return False
    if base == "to be defined":
        return True
    if other == "to be defined":
        return False
    try:
        return _parse_support_date(base) <= _parse_support_date(other)
    except ValueError as exc:
        _LOGGER.warning("Failed to parse support date: %s", exc)
        return False


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
                    assert len(dependency_package_data.status_by_version) == 1
                    support = next(
                        iter(dependency_package_data.status_by_version.values()),
                    )
                elif not dependency_package_data.has_security_policy:
                    support = dependency_package_data.status_by_version.get(
                        dependency_minor,
                        _NO_SUPPORT_DEFINED,
                    )
                else:
                    support = dependency_package_data.status_by_version.get(
                        dependency_minor,
                        _UNSUPPORTED,
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
                            or _is_supported(version_data.support, support)
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
        for (
            other_version,
            other_version_data,
        ) in other_repo_data.versions.items():
            for (
                datasource_name,
                datasource_data,
            ) in other_version_data.dependencies_by_datasource.items():
                if datasource_name not in all_datasource_names:
                    continue
                for (
                    package_name,
                    package_data,
                ) in datasource_data.versions_by_names.items():
                    for version in package_data.versions:
                        if datasource_name == "docker":
                            package_name = f"{package_name}:{version}"  # noqa: PLW2901
                        if package_name not in all_datasource_names[datasource_name]:
                            continue
                        minor_version = (
                            all_datasource_names[datasource_name][package_name]
                            if datasource_name == "docker"
                            else _canonical_minor_version(datasource_name, version)
                        )
                        version_data = repo_data.versions.get(minor_version)
                        match = False
                        if version_data is not None and datasource_name in version_data.names_by_datasource:
                            match = package_name in version_data.names_by_datasource[datasource_name].names
                        if version_data is not None and match:
                            dependencies_branches.by_branch.setdefault(
                                minor_version,
                                _Dependencies(color=_UNSUPPORTED_COLOR),
                            ).reverse.append(
                                _Dependency(
                                    name=other_repo,
                                    datasource="-",
                                    version=_clean_version(other_version),
                                    support=other_version_data.support,
                                    color=(
                                        _SUPPORTED_COLOR
                                        if _is_supported(
                                            other_version_data.support,
                                            version_data.support,
                                        )
                                        else _UNSUPPORTED_COLOR
                                    ),
                                    repo=other_repo,
                                ),
                            )
                        else:
                            dependencies_branches.by_branch.setdefault(
                                minor_version,
                                _Dependencies(),
                            ).reverse.append(
                                _Dependency(
                                    name=other_repo,
                                    datasource="-",
                                    version=_clean_version(other_version),
                                    support=other_version_data.support,
                                    color=_UNSUPPORTED_COLOR,
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
    for repo, data in context.module_config.get("additional-packages", {}).items():
        module_utils.manage_updated_separated(
            transversal_status.updated,
            transversal_status.repositories,
            repo,
            days_old=10,
        )
        pydentic_data = _TransversalStatusRepo(**data)
        transversal_status.repositories[repo] = pydentic_data
