"""Utility functions for the auto* modules."""

import datetime
import json
import logging
import os
import os.path
import re
import subprocess  # nosec
import tempfile
from typing import Any

import c2cciutils.security
import github
import requests
import toml
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


class _ReverseDependency(BaseModel):
    name: str
    status_by_version: str
    support: str
    color: str
    repo: str


class _ReverseDependencies(BaseModel):
    by_branch: dict[str, list[_ReverseDependency]] = {}


class _EventData(BaseModel):
    step: int
    branch: str | None = None


class VersionException(Exception):
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
            return [
                module.Action(
                    data=_EventData(step=1),
                )
            ]
        return []

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
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
        if context.module_event_data.step == 1:
            key = f"{context.github_project.owner}/{context.github_project.repository}"
            module_utils.manage_updated_separated(
                context.transversal_status.updated, context.transversal_status.repositories, key
            )
            status = context.transversal_status.repositories.setdefault(key, _TransversalStatusRepo())

            _update_upstream_versions(context)

            repo = context.github_project.github.get_repo(
                f"{context.github_project.owner}/{context.github_project.repository}"
            )
            stabilization_branch = []
            security_file = None
            try:
                security_file = repo.get_contents("SECURITY.md")
            except github.GithubException as exc:
                if exc.status != 404:
                    raise
            if security_file is not None:
                assert isinstance(security_file, github.ContentFile.ContentFile)
                security = c2cciutils.security.Security(security_file.decoded_content.decode("utf-8"))

                stabilization_branch = module_utils.get_stabilization_branch(security)

                version_index = security.headers.index("Version")
                support_index = security.headers.index("Supported Until")
                for raw in security.data:
                    if len(raw) > max(version_index, support_index):
                        branch = raw[version_index]
                        support = raw[support_index]
                        status.versions.setdefault(
                            branch, _TransversalStatusVersion(support=support)
                        ).support = support

            else:
                _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")
                stabilization_branch = [repo.default_branch]
                status.versions.setdefault(
                    repo.default_branch,
                    _TransversalStatusVersion(support="Best Effort"),
                ).support = "Best Effort"
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_branch))

            versions = status.versions
            for version in list(versions.keys()):
                if version not in stabilization_branch:
                    del versions[version]

            actions = []
            for branch in stabilization_branch:
                actions.append(module.Action(data=_EventData(step=2, branch=branch)))
            return ProcessOutput(actions=actions, transversal_status=context.transversal_status)
        if context.module_event_data.step == 2:
            assert context.module_event_data.branch is not None
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                success = module_utils.git_clone(context.github_project, context.module_event_data.branch)
                if not success:
                    raise VersionException("Failed to clone the repository")

                version_status = _TransversalStatusVersion(support="Best Effort")
                transversal_status = context.transversal_status
                transversal_status.repositories.setdefault(
                    f"{context.github_project.owner}/{context.github_project.repository}",
                    _TransversalStatusRepo(),
                ).versions[context.module_event_data.branch] = version_status

                _get_names(context, version_status.names_by_datasource, context.module_event_data.branch)
                message = module_utils.HtmlMessage(
                    utils.format_json(version_status.model_dump()["names_by_datasource"])
                )
                message.title = "Names:"
                _LOGGER.debug(message)
                _get_dependencies(context, version_status.dependencies_by_datasource)
                message = module_utils.HtmlMessage(
                    utils.format_json(version_status.model_dump()["dependencies_by_datasource"])
                )
                message.title = "Dependencies:"
                _LOGGER.debug(message)

                message = module_utils.HtmlMessage(
                    utils.format_json(
                        transversal_status.repositories[
                            f"{context.github_project.owner}/{context.github_project.repository}"
                        ]
                        .versions[context.module_event_data.branch]
                        .model_dump()
                    )
                )
                message.title = f"Branch ({context.module_event_data.branch}):"
                _LOGGER.debug(message)

                message = module_utils.HtmlMessage(
                    utils.format_json(
                        transversal_status.repositories[
                            f"{context.github_project.owner}/{context.github_project.repository}"
                        ].model_dump()
                    )
                )
                message.title = "Repo:"
                _LOGGER.debug(message)

                message = module_utils.HtmlMessage(utils.format_json(transversal_status.model_dump()))
                message.title = "Transversal Status:"
                _LOGGER.debug(message)

                message = module_utils.HtmlMessage(utils.format_json(context.transversal_status.model_dump()))
                message.title = "Transversal Status:"
                _LOGGER.debug(message)

            return ProcessOutput(transversal_status=context.transversal_status)
        raise VersionException("Invalid step")

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext[_TransversalStatus]
    ) -> module.TransversalDashboardOutput:
        """Get the dashboard data."""
        transversal_status = context.status

        if "repository" in context.params:
            names = _Names()
            for repo, repo_data in transversal_status.repositories.items():
                for branch, branch_data in repo_data.versions.items():
                    for datasource, datasource_data in branch_data.names_by_datasource.items():
                        for name in datasource_data.names:
                            names.by_datasources.setdefault(datasource, _NamesByDataSources()).by_package[
                                name
                            ] = _NamesStatus(repo=repo)
                            names.by_datasources[datasource].by_package[name].status_by_version[
                                _canonical_minor_version(datasource, branch)
                            ] = branch_data.support

            message = module_utils.HtmlMessage(utils.format_json(names.model_dump()))
            message.title = "Names:"
            _LOGGER.debug(message)

            # branch = list of dependencies
            reverse_dependencies = _ReverseDependencies()
            for version, version_data in transversal_status.repositories.get(
                context.params["repository"], _TransversalStatusRepo()
            ).versions.items():
                for datasource_name, dependency_data in version_data.dependencies_by_datasource.items():
                    for dependency_name, dependency_versions in dependency_data.versions_by_names.items():
                        for dependency_version in dependency_versions.versions:
                            if dependency_version.startswith("=="):
                                dependency_version = dependency_version[2:]
                            canonical_dependency_version = _canonical_minor_version(
                                dependency_name, dependency_version
                            )
                            dependency_definition: _NamesStatus = names.by_datasources.get(
                                datasource_name, _NamesByDataSources
                            ).by_package.get(
                                dependency_name,
                                _NamesStatus(
                                    status_by_version={},
                                ),
                            )
                            versions_of_dependency = dependency_definition.status_by_version
                            repo = dependency_definition.repo or "-"
                            if not versions_of_dependency:
                                continue
                            if canonical_dependency_version not in versions_of_dependency:
                                reverse_dependencies.by_branch.setdefault(version, []).append(
                                    _ReverseDependency(
                                        name=dependency_name,
                                        status_by_version=dependency_version,
                                        support="Unsupported",
                                        color="--bs-danger",
                                        repo=repo,
                                    )
                                )
                            else:
                                is_supported = all(
                                    _is_supported(
                                        support, versions_of_dependency[canonical_dependency_version]
                                    )
                                    for support in dependency_data.versions_by_names
                                )
                                reverse_dependencies.by_branch.setdefault(version, []).append(
                                    _ReverseDependency(
                                        name=dependency_name,
                                        status_by_version=dependency_version,
                                        support=versions_of_dependency[canonical_dependency_version],
                                        color="--bs-body-bg" if is_supported else "--bs-danger",
                                        repo=repo,
                                    )
                                )

            message = module_utils.HtmlMessage(utils.format_json(reverse_dependencies.model_dump()))
            message.title = "Reverse dependencies:"
            _LOGGER.debug(message)

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "reverse_dependencies": reverse_dependencies,
                    "data": utils.format_json(
                        transversal_status.repositories.get(
                            context.params["repository"], _TransversalStatusRepo()
                        ).model_dump()
                    ),
                },
            )

        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/versions/dashboard.html",
            data={"repositories": list(transversal_status.repositories.keys())},
        )


_MINOR_VERSION_RE = re.compile(r"^v?(\d+\.\d+)(\..+)?$")


def _canonical_minor_version(datasource: str, version: str) -> str:
    if datasource == "docker":
        return version

    match = _MINOR_VERSION_RE.match(version)
    if match:
        return match.group(1)
    return version


def _get_names(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    names_by_datasource: dict[str, _TransversalStatusNameByDatasource],
    branch: str,
) -> None:
    for filename in subprocess.run(  # nosec
        ["git", "ls-files", "pyproject.toml", "*/pyproject.toml"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            data = toml.load(file)
            name = data.get("project", {}).get("name")
            names = names_by_datasource.setdefault("pypi", _TransversalStatusNameByDatasource()).names
            if name and name not in names:
                names.append(name)
            else:
                name = data.get("tool", {}).get("poetry", {}).get("name")
                if name:
                    names.append(name)
    for filename in subprocess.run(  # nosec
        ["git", "ls-files", "setup.py", "*/setup.py"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            for line in file:
                match = re.match(r'^ *name ?= ?[\'"](.*)[\'"],?$', line)
                if match:
                    names_by_datasource.setdefault("pypi", _TransversalStatusNameByDatasource()).names.append(
                        match.group(1)
                    )

    if os.path.exists("ci/config.yaml"):
        with open("ci/config.yaml", encoding="utf-8") as file:
            data = yaml.load(file, Loader=yaml.SafeLoader)
            if data.get("publish", {}).get("docker", {}):
                for conf in data.get("publish", {}).get("docker", {}).get("images", []):
                    for tag in conf.get("tags", ["{version}"]):
                        names_by_datasource.setdefault(
                            "docker", _TransversalStatusNameByDatasource()
                        ).names.append(f"{conf.get('name')}:{tag.format(version=branch)}")

    for filename in subprocess.run(  # nosec
        ["git", "ls-files", "package.json", "*/package.json"],
        check=True,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            data = json.load(file)
            name = data.get("name")
            if name:
                names_by_datasource.setdefault("npm", _TransversalStatusNameByDatasource()).names.append(name)

    names_by_datasource.setdefault("github", _TransversalStatusNameByDatasource()).names.append(
        f"{context.github_project.owner}/{context.github_project.repository}"
    )


def _get_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
    result: dict[str, _TransversalStatusNameInDatasource],
) -> None:
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["renovate-graph", "--platform=local"],
        env={
            **os.environ,
            "RG_LOCAL_PLATFORM": "github",
            "RG_LOCAL_ORGANISATION": context.github_project.owner,
            "RG_LOCAL_REPO": context.github_project.repository,
        },
        capture_output=True,
        encoding="utf-8",
        timeout=300,
    )
    message = module_utils.ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Failed to get the dependencies"
        _LOGGER.error(message)
        raise VersionException(message.title)
    message.title = "Got the dependencies"
    _LOGGER.debug(message)

    lines = proc.stdout.splitlines()
    lines = [line for line in lines if line.startswith("  ")]

    index = -1
    for i, line in enumerate(lines):
        if "packageFiles" in line:
            index = i
            break
    if index != -1:
        lines = lines[index:]

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
                for dependency, datasource, version in _dependency_extractor(
                    context, dep["depName"], dep["datasource"], dep["currentValue"]
                ):
                    versions_by_names = result.setdefault(
                        datasource, _TransversalStatusNameInDatasource()
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


def _update_upstream_versions(
    context: module.ProcessContext[configuration.VersionsConfiguration, _EventData, _TransversalStatus],
) -> None:
    transversal_status = context.transversal_status
    for external_config in context.module_config.get("external-packages", []):
        package = external_config["package"]
        datasource = external_config["datasource"]

        module_utils.manage_updated_separated(
            transversal_status.updated, transversal_status.repositories, package
        )

        package_status: _TransversalStatusRepo = context.transversal_status.repositories.setdefault(
            package, _TransversalStatusRepo()
        )

        if package_status.upstream_updated and (
            package_status.upstream_updated > datetime.datetime.now() - datetime.timedelta(days=30)
        ):
            return
        package_status.upstream_updated = datetime.datetime.now()

        package_status.url = f"https://endoflife.date/{package}"
        response = requests.get(f"https://endoflife.date/api/{package}.json", timeout=10)
        if not response.ok:
            _LOGGER.error("Failed to get the data for %s", package)
            package_status.upstream_updated = None
            return
        for cycle in response.json():
            if datetime.datetime.fromisoformat(cycle["eol"]) < datetime.datetime.now():
                continue
            package_status.versions[cycle["cycle"]] = _TransversalStatusVersion(
                support=cycle["eol"],
                names_by_datasource={
                    datasource: _TransversalStatusNameByDatasource(
                        names=[package],
                    ),
                },
            )


def _is_supported(support: str, dependency_support: str) -> bool:
    if support == dependency_support:
        return True
    if support == "Unsupported":
        return True
    if dependency_support == "Unsupported":
        return False
    if support == "Best effort":
        return True
    if dependency_support == "Best effort":
        return False
    if support == "To be defined":
        return True
    if dependency_support == "To be defined":
        return False
    return datetime.datetime.fromisoformat(support) < datetime.datetime.fromisoformat(dependency_support)
