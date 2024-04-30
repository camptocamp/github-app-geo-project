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

from github_app_geo_project import module, utils
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.versions import configuration

_LOGGER = logging.getLogger(__name__)


class VersionException(Exception):
    """Error while updating the versions."""


class Versions(module.Module[configuration.VersionsConfiguration]):
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
        self, context: module.ProcessContext[configuration.VersionsConfiguration]
    ) -> module.ProcessOutput | None:
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
        """Get the dashboard data."""
        if "repository" in context.params:
            # datasource.package.minor_version = support
            names: dict[str, dict[str, dict[str, str]]] = {}
            for repo_data in context.status.values():
                for branch, branch_data in repo_data.get("versions", {}).items():
                    for datasource, datasource_data in branch_data.get("names", {}).items():
                        for name, versions in datasource_data.items():
                            names.setdefault(datasource, {}).setdefault(name, {})[
                                _canonical_minor_version(datasource, branch)
                            ] = branch_data.get("support")

            message = module_utils.HtmlMessage(utils.format_json(names))
            message.title = "Names:"
            _LOGGER.debug(message)

            # branch = list of dependencies
            reverse_dependencies: dict[str, list[dict[str, str]]] = {}
            for version, datasource_data in (
                context.status.get(context.params["repository"], {}).get("versions", {}).items()
            ):
                for datasource, datasource_data in datasource_data.get("dependencies", {}).items():
                    for package, versions in datasource_data.items():
                        for version in versions:
                            canonical_version = _canonical_minor_version(package, version)
                            dependency_versions = names.get(datasource, {}).get(package, {})
                            if not dependency_versions:
                                continue
                            if canonical_version not in dependency_versions:
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": package,
                                        "version": version,
                                        "support": "Unsupported",
                                        "color": "--bs-danger",
                                    }
                                )
                            else:
                                is_supported = _is_supported(
                                    datasource_data["support"], dependency_versions[version]
                                )
                                reverse_dependencies.setdefault(version, []).append(
                                    {
                                        "name": package,
                                        "versions": version,
                                        "support": dependency_versions[version],
                                        "color": "--bs-body-bg" if is_supported else "--bs-danger",
                                    }
                                )

            message = module_utils.HtmlMessage(utils.format_json(reverse_dependencies))
            message.title = "Reverse dependencies:"
            _LOGGER.debug(message)

            context.status.get(context.params["repository"], {})

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "reverse_dependencies": reverse_dependencies,
                    "data": utils.format_json(context.status.get(context.params["repository"], {})),
                },
            )

        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/versions/dashboard.html",
            data={"repositories": context.status.keys()},
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
    context: module.ProcessContext[configuration.VersionsConfiguration], names: dict[str, Any], branch: str
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
    context: module.ProcessContext[configuration.VersionsConfiguration], result: dict[str, dict[str, Any]]
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
                    result.setdefault(datasource, {}).setdefault(dependency, set()).add(version)

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.items():
            datasource_value[dep] = list(dep_value)


def _dependency_extractor(
    context: module.ProcessContext[configuration.VersionsConfiguration],
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


def _update_upstream_versions(context: module.ProcessContext[configuration.VersionsConfiguration]) -> None:
    for external_config in context.module_config.get("external-packages", []):
        package = external_config["package"]
        datasource = external_config["datasource"]
        package_status = context.transversal_status.setdefault(package, {})

        module_utils.manage_updated(context.transversal_status, package)
        if "upstream-updated" in package_status and datetime.datetime.fromisoformat(
            package_status["upstream-updated"]
        ) > datetime.datetime.now() - datetime.timedelta(days=30):
            return
        context.transversal_status["upstream-updated"][
            "upstream-updated"
        ] = datetime.datetime.now().isoformat()

        package_status["url"] = f"https://endoflife.date/{package}"
        response = requests.get(f"https://endoflife.date/api/{package}.json", timeout=10)
        if not response.ok:
            _LOGGER.error("Failed to get the data for %s", package)
            if "upstream-updated" in context.transversal_status["upstream-updated"]:
                del context.transversal_status["upstream-updated"]["upstream-updated"]
            return
        for cycle in response.json():
            if datetime.datetime.fromisoformat(cycle["eol"]) < datetime.datetime.now():
                continue
            package_status.setdefault("versions", {})[cycle["cycle"]] = {
                "support": cycle["eol"],
                "names": {
                    datasource: {
                        package: [cycle["latest"]],
                    },
                },
            }


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
