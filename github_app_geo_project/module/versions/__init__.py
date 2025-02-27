"""Utility functions for the auto* modules."""

import asyncio
import datetime
import json
import logging
import os
import os.path
import re
import tempfile
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import aiofiles
import aiohttp
import c2cciutils.configuration
import github
import security_md
import yaml
from pydantic import BaseModel

from github_app_geo_project import module, utils
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.versions import configuration

_LOGGER = logging.getLogger(__name__)


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


class _TransversalStatus(BaseModel):
    updated: dict[str, datetime.datetime] = {}
    """Repository updated time"""
    repositories: dict[str, _TransversalStatusRepo] = {}


class _NamesStatus(BaseModel):
    status_by_version: dict[str, str] = {}
    repo: str | None = None


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
    support: str = "Unsupported"
    forward: list[_Dependency] = []
    reverse: list[_Dependency] = []


class _DependenciesBranches(BaseModel):
    by_branch: dict[str, _Dependencies] = {}


class _EventData(BaseModel):
    step: int
    version: str | None = None
    alternate_versions: list[str] | None = None


class VersionError(Exception):
    """Error while updating the versions."""


class Versions(module.Module[configuration.VersionsConfiguration, _EventData, _TransversalStatus]):
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

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.event_data.get("type") == "event" and context.event_data.get("name") == "daily":
            return [module.Action(data=_EventData(step=1), priority=module.PRIORITY_CRON)]
        return []

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        with (Path(__file__).parent / "schema.json").open(encoding="utf-8") as schema_file:
            schema = json.loads(schema_file.read())
            for key in ("$schema", "$id"):
                if key in schema:
                    del schema[key]
            return schema  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(permissions={"contents": "read"}, events=set())

    async def process(
        self,
        context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    ) -> module.ProcessOutput[_EventData, _TransversalStatus]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        key = f"{context.github_project.owner}/{context.github_project.repository}"
        status = context.transversal_status.repositories.setdefault(key, _TransversalStatusRepo())
        if context.module_event_data.step == 1:
            status.url = (
                f"https://github.com/{context.github_project.owner}/{context.github_project.repository}"
            )
            module_utils.manage_updated_separated(
                context.transversal_status.updated,
                context.transversal_status.repositories,
                key,
            )

            _apply_additional_packages(context)
            await _update_upstream_versions(context)

            repo = context.github_project.repo
            stabilization_versions = []
            security_file = None
            security = None
            try:
                security_file = repo.get_contents("SECURITY.md")
            except github.GithubException as exc:
                if exc.status != 404:
                    raise
            if security_file is not None:
                assert isinstance(security_file, github.ContentFile.ContentFile)
                security = security_md.Security(security_file.decoded_content.decode("utf-8"))

                stabilization_versions = module_utils.get_stabilization_versions(security)
            else:
                _LOGGER.debug("No SECURITY.md file in the repository, apply only on default branch")

            stabilization_versions.append(repo.default_branch)
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_versions))
            for version in stabilization_versions:
                status.versions.setdefault(
                    version,
                    _TransversalStatusVersion(support="Best effort"),
                ).support = "Best effort"

            if security is not None:
                version_index = security.version_index
                support_index = security.support_until_index
                for raw in security.data:
                    if len(raw) > max(version_index, support_index):
                        version = raw[version_index]
                        support = raw[support_index]
                        if version in status.versions:
                            status.versions[version].support = support

            versions = status.versions
            for version in list(versions.keys()):
                if version not in stabilization_versions:
                    del versions[version]

            actions = []
            for version in stabilization_versions:
                actions.append(
                    module.Action(
                        data=_EventData(
                            step=2,
                            version=version,
                            alternate_versions=(
                                module_utils.get_alternate_versions(security, version)
                                if security is not None
                                else []
                            ),
                        ),
                        title=version,
                        priority=module.PRIORITY_CRON,
                    ),
                )
            return ProcessOutput(actions=actions, transversal_status=context.transversal_status)
        if context.module_event_data.step == 2:
            assert context.module_event_data.version is not None
            version = context.module_event_data.version
            branch = context.module_config.get("version-mapping", {}).get(version, version)
            async with module_utils.WORKING_DIRECTORY_LOCK:
                with tempfile.TemporaryDirectory() as tmpdirname:
                    if os.environ.get("TEST") != "TRUE":
                        os.chdir(tmpdirname)
                        success = await module_utils.git_clone(context.github_project, branch)
                        if not success:
                            exception_message = "Failed to clone the repository"
                            raise VersionError(exception_message)

                    version_status = status.versions[version]
                    version_status.names_by_datasource.clear()
                    version_status.dependencies_by_datasource.clear()
                    transversal_status = context.transversal_status

                    message = module_utils.HtmlMessage(
                        utils.format_json(
                            json.loads(version_status.model_dump_json())["names_by_datasource"]
                        ),
                    )
                    message.title = "Names cleaned:"

                    await _get_names(
                        context,
                        version_status.names_by_datasource,
                        version,
                        alternate_versions=context.module_event_data.alternate_versions,
                    )
                    message = module_utils.HtmlMessage(
                        utils.format_json(
                            json.loads(version_status.model_dump_json())["names_by_datasource"]
                        ),
                    )
                    message.title = "Names:"
                    _LOGGER.debug(message)
                    await _get_dependencies(context, version_status.dependencies_by_datasource)
                    message = module_utils.HtmlMessage(
                        utils.format_json(
                            json.loads(version_status.model_dump_json())["dependencies_by_datasource"],
                        ),
                    )
                    message.title = "Dependencies:"
                    _LOGGER.debug(message)

                    message = module_utils.HtmlMessage(
                        utils.format_json_str(
                            transversal_status.repositories[
                                f"{context.github_project.owner}/{context.github_project.repository}"
                            ]
                            .versions[version]
                            .model_dump_json(indent=2),
                        ),
                    )
                    message.title = f"Version ({version}):"
                    _LOGGER.debug(message)

                    message = module_utils.HtmlMessage(
                        utils.format_json_str(
                            transversal_status.repositories[
                                f"{context.github_project.owner}/{context.github_project.repository}"
                            ].model_dump_json(indent=2),
                        ),
                    )
                    message.title = "Repo:"
                    _LOGGER.debug(message)

            return ProcessOutput(transversal_status=context.transversal_status)
        raise VersionError("Invalid step")

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
                    for datasource, datasource_data in branch_data.names_by_datasource.items():
                        for name in datasource_data.names:
                            current_status = names.by_datasources.setdefault(
                                datasource,
                                _NamesByDataSources(),
                            ).by_package.setdefault(name, _NamesStatus(repo=repo))
                            current_status.status_by_version[_canonical_minor_version(datasource, branch)] = (
                                branch_data.support
                            )

            message = module_utils.HtmlMessage(utils.format_json_str(names.model_dump_json()))
            message.title = "Names:"
            _LOGGER.debug(message)

            # branch = list of dependencies
            dependencies_branches = _DependenciesBranches()
            for version, version_data in transversal_status.repositories.get(
                context.params["repository"],
                _TransversalStatusRepo(),
            ).versions.items():
                _build_internal_dependencies(version, version_data, names, dependencies_branches)
            _build_reverse_dependency(
                context.params["repository"],
                transversal_status.repositories.get(context.params["repository"], _TransversalStatusRepo()),
                transversal_status,
                dependencies_branches,
            )

            message = module_utils.HtmlMessage(utils.format_json_str(dependencies_branches.model_dump_json()))
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
    _VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")

    def __init__(self, version: str) -> None:
        self.version = version

    def __cmp__(self, other: "_Version") -> int:
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
        return self.__cmp__(other) < 0


def _order_versions(versions: Iterable[str]) -> list[str]:
    return sorted(versions, reverse=True, key=_Version)


def _clean_version(version: str) -> str:
    return version.lstrip("v=")


def _canonical_minor_version(datasource: str, version: str) -> str:
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
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    names_by_datasource: dict[str, _TransversalStatusNameByDatasource],
    version: str,
    alternate_versions: list[str] | None = None,
) -> None:
    command = ["git", "ls-files", "pyproject.toml", "*/pyproject.toml"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
            async with aiofiles.open(filename, encoding="utf-8") as file:
                data = tomllib.loads(await file.read())
                name = data.get("project", {}).get("name")
                names = names_by_datasource.setdefault("pypi", _TransversalStatusNameByDatasource()).names
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
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
            async with aiofiles.open(filename, encoding="utf-8") as file:
                names = names_by_datasource.setdefault("pypi", _TransversalStatusNameByDatasource()).names
                async for line in file:
                    match = re.match(r'^ *name ?= ?[\'"](.*)[\'"],?$', line)
                    if match and match.group(1) not in names:
                        names.append(match.group(1))
    os.environ["GITHUB_REPOSITORY"] = f"{context.github_project.owner}/{context.github_project.repository}"
    docker_config = {}
    if Path(".github/publish.yaml").exists():
        async with aiofiles.open(".github/publish.yaml", encoding="utf-8") as file:
            docker_config = yaml.load(await file.read(), Loader=yaml.SafeLoader).get("docker", {})
    else:
        data = c2cciutils.get_config()
        docker_config = data.get("publish", {}).get("docker", {})
    if docker_config:
        names = names_by_datasource.setdefault("docker", _TransversalStatusNameByDatasource()).names
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
                        repository_server = repository_conf.get("server", False)
                        add_names = []
                        if repository_server:
                            add_names.append(
                                f"{repository_server}/{conf.get('name')}:{tag.format(version=ver)}",
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
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
            with Path(filename).open(encoding="utf-8") as file:
                data = json.load(file)
                name = data.get("name")
                names = names_by_datasource.setdefault("npm", _TransversalStatusNameByDatasource()).names
                if name and name not in names:
                    names.append(name)

    names = names_by_datasource.setdefault("github-release", _TransversalStatusNameByDatasource()).names
    add_name = f"{context.github_project.owner}/{context.github_project.repository}"
    if add_name not in names:
        names.append(add_name)


async def _get_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    result: dict[str, _TransversalStatusNameInDatasource],
) -> None:
    if os.environ.get("TEST") != "TRUE":
        command = ["renovate-graph", "--platform=local"]
        proc = await asyncio.create_subprocess_exec(  # pylint: disable=subprocess-run-check
            *command,
            env={
                **os.environ,
                "RG_LOCAL_PLATFORM": "github",
                "RG_LOCAL_ORGANISATION": context.github_project.owner,
                "RG_LOCAL_REPO": context.github_project.repository,
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        message: module_utils.HtmlMessage = module_utils.AnsiProcessMessage.from_async_artifacts(
            command,
            proc,
            stdout,
            stderr,
        )
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
        if '"packageFiles": {' in line and line.startswith("  "):
            index = i
            break
    if index != -1:
        lines = lines[index:]

    index = -1
    for i, line in enumerate(lines):
        if not line.startswith("  "):
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


def _read_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    data: dict[str, Any],
    result: dict[str, _TransversalStatusNameInDatasource],
) -> None:
    for values in data.get("packageFiles", {}).values():
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
                    versions = versions_by_names.setdefault(dependency, _TransversalStatusVersions()).versions
                    if version not in versions:
                        versions.append(version)

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.versions_by_names.items():
            datasource_value.versions_by_names[dep] = dep_value


def _dependency_extractor(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    dependency: str,
    datasource: str,
    version: str,
) -> list[tuple[str, str, str]]:
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
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
) -> None:
    transversal_status = context.transversal_status
    for external_config in context.module_config.get("external-packages", []):
        package = external_config["package"]
        name = f"endoflife.date/{package}"
        datasource = external_config["datasource"]

        module_utils.manage_updated_separated(
            transversal_status.updated,
            transversal_status.repositories,
            name,
        )

        package_status: _TransversalStatusRepo = context.transversal_status.repositories.setdefault(
            name,
            _TransversalStatusRepo(),
        )
        package_status.url = f"https://endoflife.date/{package}"

        if package_status.upstream_updated and (
            package_status.upstream_updated
            > datetime.datetime.now(datetime.UTC)
            - utils.parse_duration(os.environ.get("GHCI_EXTERNAL_PACKAGES_UPDATE_PERIOD", "30d"))
        ):
            return
        package_status.upstream_updated = datetime.datetime.now(datetime.UTC)

        async with (
            aiohttp.ClientSession() as session,
            asyncio.timeout(10),
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
                if datetime.datetime.fromisoformat(eol) < datetime.datetime.now(datetime.UTC):
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
    try:
        return datetime.datetime.fromisoformat(text).replace(
            tzinfo=datetime.UTC,
        )
    except ValueError:
        # Parse date like 01/01/2024
        return datetime.datetime.strptime(text, "%d/%m/%Y").replace(
            tzinfo=datetime.UTC,
        )


def _is_supported(base: str, other: str) -> bool:
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
    dependencies_branch = dependencies_branches.by_branch.setdefault(
        version,
        _Dependencies(support=version_data.support),
    )
    for datasource_name, dependencies_data in version_data.dependencies_by_datasource.items():
        for dependency_name, dependency_versions in dependencies_data.versions_by_names.items():
            if datasource_name not in names.by_datasources:
                continue
            for dependency_version in dependency_versions.versions:
                dependency_data = names.by_datasources[datasource_name]
                if datasource_name == "docker":
                    full_dependency_name = f"{dependency_name}:{dependency_version}"
                else:
                    full_dependency_name = dependency_name
                if full_dependency_name not in dependency_data.by_package:
                    continue
                dependency_package_data = dependency_data.by_package[full_dependency_name]
                dependency_minor = _canonical_minor_version(datasource_name, dependency_version)
                if datasource_name == "docker":
                    assert len(dependency_package_data.status_by_version) == 1
                    support = next(iter(dependency_package_data.status_by_version.values()))
                else:
                    support = dependency_package_data.status_by_version.get(
                        dependency_minor,
                        "Unsupported",
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
                            "--bs-body-bg" if _is_supported(version_data.support, support) else "--bs-danger"
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
    all_datasource_names: dict[str, dict[str, str]] = {}
    for branch, version_name_data in repo_data.versions.items():
        for datasource_name, datasource_name_data in version_name_data.names_by_datasource.items():
            for package_name in datasource_name_data.names:
                all_datasource_names.setdefault(datasource_name, {})[package_name] = branch
    for other_repo, other_repo_data in transversal_status.repositories.items():
        if repository == other_repo:
            continue
        for (
            other_version,
            other_version_data,
        ) in other_repo_data.versions.items():
            for datasource_name, datasource_data in other_version_data.dependencies_by_datasource.items():
                if datasource_name not in all_datasource_names:
                    continue
                for package_name, package_data in datasource_data.versions_by_names.items():
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
                                _Dependencies(),
                            ).reverse.append(
                                _Dependency(
                                    name=other_repo,
                                    datasource="-",
                                    version=_clean_version(other_version),
                                    support=other_version_data.support,
                                    color=(
                                        "--bs-body-bg"
                                        if _is_supported(other_version_data.support, version_data.support)
                                        else "--bs-danger"
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
                                    color="--bs-danger",
                                    repo=other_repo,
                                ),
                            )


def _apply_additional_packages(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
) -> None:
    for repo, data in context.module_config.get("additional-packages", {}).items():
        module_utils.manage_updated_separated(
            context.transversal_status.updated,
            context.transversal_status.repositories,
            repo,
        )
        pydentic_data = _TransversalStatusRepo(**data)
        context.transversal_status.repositories[repo] = pydentic_data
