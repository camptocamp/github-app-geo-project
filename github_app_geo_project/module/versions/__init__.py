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
import pygments.formatters
import pygments.lexers
import requests
import toml
import yaml

from github_app_geo_project import module
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class VersionException(Exception):
    """Error while updating the versions."""


class Versions(module.Module[dict[str, None]]):
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

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
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
        return {
            "type": "object",
            "properties": {},
        }

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        return module.GitHubApplicationPermissions(permissions={"contents": "read"}, events=set())

    def process(self, context: module.ProcessContext[dict[str, None]]) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        if context.module_data.get("step") == 1:
            _update_upstream_versions(context)

            status = context.transversal_status.setdefault(
                f"{context.github_project.owner}/{context.github_project.repository}", {}
            )

            module_utils.manage_updated(
                context.transversal_status,
                f"{context.github_project.owner}/{context.github_project.repository}",
            )

            status["versions"] = {}
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
                        status["versions"].setdefault(branch, {})["support"] = support

            else:
                _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")
                stabilization_branch = [repo.default_branch]
                status["versions"].setdefault(repo.default_branch, {})["support"] = "Best Effort"
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_branch))

            versions = status.setdefault("versions", {})
            for version in list(versions.keys()):
                if version not in stabilization_branch:
                    del versions[version]

            actions = []
            for branch in stabilization_branch:
                actions.append(module.Action(data={"step": 2, "branch": branch}))
            return ProcessOutput(actions=actions, transversal_status=context.transversal_status)
        if context.module_data.get("step") == 2:
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                success = module_utils.git_clone(context.github_project, context.module_data["branch"])
                if not success:
                    raise VersionException("Failed to clone the repository")

                status = (
                    context.transversal_status.setdefault(
                        f"{context.github_project.owner}/{context.github_project.repository}", {}
                    )
                    .setdefault("versions", {})
                    .setdefault(context.module_data["branch"], {})
                )
                _get_names(context, status.setdefault("names", {}), context.module_data["branch"])
                _get_dependencies(context, status.setdefault("dependencies", {}))
            return ProcessOutput(transversal_status=context.transversal_status)
        raise VersionException("Invalid step")

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext
    ) -> module.TransversalDashboardOutput:
        if "repository" in context.params:
            lexer = pygments.lexers.JsonLexer()
            formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")

            names: dict[str, dict[str, dict[str, str]]] = {}
            for repo_data in context.status.values():
                for branch, branch_data in repo_data.get("versions", {}).items():
                    for version_type, version_data in branch_data.get("names", {}).items():
                        for name, versions in version_data.items():
                            names.setdefault(version_type, {}).setdefault(name, {})[branch] = branch_data.get(
                                "support"
                            )

            formatted = pygments.highlight(json.dumps(names, indent=4), lexer, formatter)
            message = module_utils.HtmlMessage(formatted)
            message.title = "Names:"
            _LOGGER.debug(message)

            reverse_dependencies: dict[str, list[dict[str, str]]] = {}
            for version, version_data in (
                context.status.get(context.params["repository"], {}).get("versions", {}).items()
            ):
                for dependency_type, dependency_data in version_data.get("dependencies", {}).items():
                    for dependency_name, versions in dependency_data.items():
                        for version in versions:
                            dependency_versions = names.get(dependency_type, {}).get(dependency_name, {})
                            if not dependency_versions:
                                continue
                            if version not in dependency_versions:
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": dependency_name,
                                        "versions": version,
                                        "support": "Unsupported",
                                        "color": "--bs-danger",
                                    }
                                )
                            else:
                                is_supported = _is_supported(
                                    version_data["support"], dependency_versions[version]
                                )
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": dependency_name,
                                        "versions": version,
                                        "support": dependency_versions[version],
                                        "color": "--bs-body-bg" if is_supported else "--bs-danger",
                                    }
                                )

            formatted = pygments.highlight(json.dumps(reverse_dependencies, indent=4), lexer, formatter)
            message = module_utils.HtmlMessage(formatted)
            message.title = "Reverse dependencies:"
            _LOGGER.debug(message)

            context.status.get(context.params["repository"], {})

            lexer = pygments.lexers.JsonLexer()
            formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")
            data = pygments.highlight(
                json.dumps(context.status.get(context.params["repository"], {}), indent=4), lexer, formatter
            )

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "reverse_dependencies": reverse_dependencies,
                    "data": data,
                },
            )

        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/versions/dashboard.html",
            data={"repositories": context.status.keys()},
        )


def _get_names(context: module.ProcessContext[dict[str, None]], names: dict[str, Any], branch: str) -> None:
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
    context: module.ProcessContext[dict[str, None]], result: dict[str, dict[str, Any]]
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
    data = json.loads(json_str)
    for values in data.get("packageFiles", {}).values():
        for value in values:
            for dep in value.get("deps", []):
                if "currentValue" not in dep:
                    continue
                result.setdefault(dep.get("datasource", "-"), {}).setdefault(
                    dep.get("depName", "-"), set()
                ).add(dep.get("currentValue"))

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.items():
            datasource_value[dep] = list(dep_value)


def _update_upstream_versions(context: module.ProcessContext[dict[str, None]]) -> None:
    module_utils.manage_updated(context.transversal_status, "upstream-updated")
    upstream_versions = context.transversal_status.setdefault("upstream-updated", {})
    if "upstream-updated" in upstream_versions and datetime.datetime.fromisoformat(
        upstream_versions["upstream-updated"]
    ) > datetime.datetime.now() - datetime.timedelta(days=30):
        return

    for package, version_type in {
        "python": "pypi",
        "ubuntu": "docker",
        "debian": "docker",
        "node": "node-version",
    }.items():

        module_utils.manage_updated(context.transversal_status, package)
        python_status = context.transversal_status.setdefault("python", {})
        for cycle in requests.get(f"https://endoflife.date/{package}.json", timeout=10).json():
            python_status.setdefault("versions", {})[cycle["version"]] = {
                "support": cycle["eol"],
                "names": {
                    version_type: {
                        package: [cycle["version"]],
                    },
                },
            }

    module_utils.manage_updated(context.transversal_status, "camptocamp/postgres")
    postgres_status = context.transversal_status.setdefault("camptocamp/postgres", {})
    for cycle in requests.get("https://endoflife.date/postgresql.json", timeout=30).json():
        tag = {
            "12": "12-postgis-3",
            "13": "13-postgis-3",
            "14": "14-postgis-3",
            "15": "15-postgis-3",
            "16": "16-postgis-3",
        }
        postgres_status.setdefault("versions", {})[cycle["version"]] = {
            "support": cycle["eol"],
            "names": {
                "docker": {
                    "camptocamp/postgres": [tag[cycle["version"]]],
                },
            },
        }

    module_utils.manage_updated(context.transversal_status, "osgeo/gdal")
    gdal_status = context.transversal_status.setdefault("osgeo/gdal", {})
    gdal_status.update(
        {
            "versions": {
                "3.3": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.3"]}}},
                "3.4": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.4"]}}},
                "3.5": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.5"]}}},
                "3.6": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.6"]}}},
                "3.7": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.7"]}}},
                "3.8": {"support": "Best effort", "names": {"docker": {"osgeo/gdal": ["3.8"]}}},
            }
        }
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
