"""Utility functions for the auto* modules."""

import datetime
import json
import logging
import os
import os.path
import re
import subprocess  # nosec
import tempfile
from typing import Any, NotRequired, TypedDict

import c2cciutils.security
import github
import requests
import toml
import yaml
from pydantic import BaseModel, ConfigDict

from github_app_geo_project import module, utils
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.versions import configuration

_LOGGER = logging.getLogger(__name__)


class _Dependency(TypedDict):
    """Dependency."""

    versions: dict[str, str]
    repo: NotRequired[str]


class _TransversalStatusVersion(BaseModel):
    support: str
    names: dict[str, dict[str, list[str]]] = {}
    """Datasource.name.branch[]"""
    dependencies: dict[str, dict[str, list[str]]] = {}
    """Datasource.name.versions[]"""


class _TransversalStatusRepo(BaseModel):
    """Transversal dashboard repo."""

    updated: datetime.datetime | None = None
    versions: dict[str, _TransversalStatusVersion] = {}
    upstream_updated: datetime.datetime | None = None
    url: str | None = None
    support: str | None = None


class _TransversalStatus(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, _TransversalStatusRepo]


class VersionException(Exception):
    """Error while updating the versions."""


class Versions(module.Module[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]]):
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

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[dict[str, Any]]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if context.event_data.get("type") == "event" and context.event_data.get("name") == "daily":
            return [
                module.Action(
                    data={"step": 1},
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
        context: module.ProcessContext[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]],
    ) -> module.ProcessOutput[dict[str, Any], dict[str, Any]]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        if context.module_event_data.get("step") == 1:
            key = f"{context.github_project.owner}/{context.github_project.repository}"
            module_utils.manage_updated(context.transversal_status, key)
            context.transversal_status[key].setdefault("versions", {})
            transversal_status = _TransversalStatus(**context.transversal_status)
            extra = transversal_status.__pydantic_extra__
            status: _TransversalStatusRepo = extra[key]  # pylint: disable=unsubscriptable-object

            transversal_status = _update_upstream_versions(context, transversal_status)

            status.versions = {}
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
                actions.append(module.Action(data={"step": 2, "branch": branch}))
            return ProcessOutput(actions=actions, transversal_status=transversal_status.model_dump())
        if context.module_event_data.get("step") == 2:
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                success = module_utils.git_clone(context.github_project, context.module_event_data["branch"])
                if not success:
                    raise VersionException("Failed to clone the repository")

                version_status = _TransversalStatusVersion(support="Best Effort")

                transversal_status = _TransversalStatus(**context.transversal_status)
                transversal_status.__pydantic_extra__.setdefault(  # pylint: disable=no-member
                    f"{context.github_project.owner}/{context.github_project.repository}",
                    _TransversalStatusRepo(updated=datetime.datetime.now()),
                ).versions[context.module_event_data["branch"]] = version_status
                _get_names(context, version_status.names, context.module_event_data["branch"])
                _get_dependencies(context, version_status.dependencies)
            return ProcessOutput(transversal_status=transversal_status.model_dump())
        raise VersionException("Invalid step")

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext[dict[str, Any]]
    ) -> module.TransversalDashboardOutput:
        """Get the dashboard data."""
        transversal_status = _TransversalStatus(**context.status)

        if "repository" in context.params:
            # datasource.package.minor_version = support
            names: dict[str, dict[str, _Dependency]] = {}
            for repo, repo_data in transversal_status.__pydantic_extra__.items():  # pylint: disable=no-member
                for branch, branch_data in repo_data.versions.items():
                    for datasource, datasource_data in branch_data.names.items():
                        for name, dependency_versions in datasource_data.items():
                            names.setdefault(datasource, {}).setdefault(name, {"repo": repo, "versions": {}})
                            names[datasource][name]["versions"][
                                _canonical_minor_version(datasource, branch)
                            ] = branch_data.support

            message = module_utils.HtmlMessage(utils.format_json(names))
            message.title = "Names:"
            _LOGGER.debug(message)

            # branch = list of dependencies
            reverse_dependencies: dict[str, list[dict[str, str]]] = {}
            for version, version_data in transversal_status.__pydantic_extra__.get(
                context.params["repository"], _TransversalStatusRepo()
            ).versions.items():
                for datasource, datasource_data in version_data.dependencies.items():
                    for dependency, dependency_versions in datasource_data.items():
                        for dependency_version in dependency_versions:
                            if dependency_version.startswith("=="):
                                dependency_version = dependency_version[2:]
                            canonical_dependency_version = _canonical_minor_version(
                                dependency, dependency_version
                            )
                            dependency_definition: _Dependency = names.get(datasource, {}).get(
                                dependency,
                                {
                                    "versions": {},
                                },
                            )
                            versions_of_dependency = dependency_definition.get("versions", {})
                            repo = dependency_definition.get("repo", "-")
                            if not versions_of_dependency:
                                continue
                            if canonical_dependency_version not in versions_of_dependency:
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": dependency,
                                        "version": dependency_version,
                                        "support": "Unsupported",
                                        "color": "--bs-danger",
                                        "repo": repo,
                                    }
                                )
                            else:
                                is_supported = all(
                                    _is_supported(support, versions_of_dependency[version])
                                    for support in datasource_data["support"]
                                )
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": dependency,
                                        "versions": dependency_version,
                                        "support": versions_of_dependency[version],
                                        "color": "--bs-body-bg" if is_supported else "--bs-danger",
                                        "repo": repo,
                                    }
                                )

            message = module_utils.HtmlMessage(utils.format_json(reverse_dependencies))
            message.title = "Reverse dependencies:"
            _LOGGER.debug(message)

            transversal_status.__pydantic_extra__.get(context.params["repository"], {})

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "reverse_dependencies": reverse_dependencies,
                    "data": utils.format_json(
                        transversal_status.__pydantic_extra__.get(
                            context.params["repository"], _TransversalStatusRepo()
                        ).model_dump()
                    ),
                },
            )

        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/versions/dashboard.html",
            data={"repositories": transversal_status.__pydantic_extra__.keys()},  # pylint: disable=no-member
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
    context: module.ProcessContext[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]],
    names: dict[str, dict[str, list[str]]],
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
            if name:
                names.setdefault("pypi", {}).setdefault(name, []).append(branch)
            else:
                name = data.get("tool", {}).get("poetry", {}).get("name")
                if name:
                    names.setdefault("pypi", {}).setdefault(name, []).append(branch)
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
                    names.setdefault("pypi", {}).setdefault(match.group(1), []).append(branch)

    if os.path.exists("ci/config.yaml"):
        with open("ci/config.yaml", encoding="utf-8") as file:
            data = yaml.load(file, Loader=yaml.SafeLoader)
            if data.get("publish", {}).get("docker", {}):
                for conf in data.get("publish", {}).get("docker", {}).get("images", []):
                    for tag in conf.get("tags", ["{version}"]):
                        names.setdefault("docker", {}).setdefault(conf["name"], []).append(
                            tag.format(version=branch)
                        )

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
                names.setdefault("npm", {}).setdefault(name, []).append(branch)

    names.setdefault("github", {}).setdefault(
        f"{context.github_project.owner}/{context.github_project.repository}", []
    ).append(branch)


def _get_dependencies(
    context: module.ProcessContext[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]],
    result: dict[str, dict[str, list[str]]],
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
        _LOGGER.error(message.to_html(style="collapse"))
        raise VersionException(message.title)
    message.title = "Got the dependencies"
    _LOGGER.debug(message.to_html(style="collapse"))

    lines = proc.stdout.splitlines()
    lines = [line for line in lines if line.startswith("  ")]

    index = -1
    for i, line in enumerate(lines):
        if "packageFiles" in line:
            index = i
            break
    if index != -1:
        lines = lines[index:]

    json_str = "{\n" + "".join(lines) + "}\n"
    message = module_utils.HtmlMessage(utils.format_json_str(json_str))
    message.title = "Read dependencies from"
    _LOGGER.debug(message)
    data = json.loads(json_str)

    for values in data.get("packageFiles", {}).values():
        for value in values:
            for dep in value.get("deps", []):
                if "currentValue" not in dep:
                    continue
                for dependency, datasource, version in _dependency_extractor(
                    context, dep["depName"], dep["datasource"], dep["currentValue"]
                ):
                    result.setdefault(datasource, {}).setdefault(dependency, []).append(version)

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.items():
            datasource_value[dep] = list(dep_value)


def _dependency_extractor(
    context: module.ProcessContext[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]],
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
    context: module.ProcessContext[configuration.VersionsConfiguration, dict[str, Any], dict[str, Any]],
    transversal_status: _TransversalStatus,
) -> _TransversalStatus:
    for external_config in context.module_config.get("external-packages", []):
        package = external_config["package"]
        datasource = external_config["datasource"]

        transversal_status_dict = transversal_status.model_dump()
        module_utils.manage_updated(transversal_status_dict, package)
        transversal_status = _TransversalStatus(**transversal_status_dict)

        package_status: _TransversalStatusRepo = (
            transversal_status.__pydantic_extra__.setdefault(  # pylint: disable=no-member
                package, _TransversalStatusRepo()
            )
        )

        if package_status.upstream_updated and (
            package_status.upstream_updated > datetime.datetime.now() - datetime.timedelta(days=30)
        ):
            return transversal_status
        package_status.upstream_updated = datetime.datetime.now()

        package_status.url = f"https://endoflife.date/{package}"
        response = requests.get(f"https://endoflife.date/api/{package}.json", timeout=10)
        if not response.ok:
            _LOGGER.error("Failed to get the data for %s", package)
            package_status.upstream_updated = None
            return transversal_status
        for cycle in response.json():
            if datetime.datetime.fromisoformat(cycle["eol"]) < datetime.datetime.now():
                continue
            package_status.versions[cycle["cycle"]] = _TransversalStatusVersion(
                support=cycle["eol"],
                names={
                    datasource: {
                        package: [cycle["latest"]],
                    },
                },
            )

    return transversal_status


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
