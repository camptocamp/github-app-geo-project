"""Utility functions for the auto* modules."""

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
import toml
import yaml

from github_app_geo_project import module
from github_app_geo_project.module import ProcessOutput
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class Version(module.Module[dict[str, None]]):
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
        return [
            module.Action(
                data={"step": 1},
            )
        ]

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module configuration."""
        return {
            "type": "object",
            "properties": {},
        }

    def process(self, context: module.ProcessContext[dict[str, None]]) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        if context.module_data.get("step") == 1:
            status = context.transversal_status.setdefault(
                f"{context.github_project.owner}/{context.github_project.repository}", {}
            )
            status["versions"] = {}
            repo = context.github_project.github.get_repo(
                f"{context.github_project.owner}/{context.github_project.repository}"
            )
            security_file = repo.get_contents("SECURITY.md")
            stabilization_branch = []
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
                        status["versions"][branch] = support

            else:
                _LOGGER.debug("No SECURITY.md file in the repository, apply on default branch")
                stabilization_branch = [repo.default_branch]
                status["versions"][repo.default_branch] = "Best Effort"
            _LOGGER.debug("Versions: %s", ", ".join(stabilization_branch))

            versions = status.setdefault("versions", {})
            for version in list(versions.keys()):
                if version not in stabilization_branch:
                    del versions[version]

            actions = []
            for branch in stabilization_branch:
                actions.append(module.Action(data={"step": 2, "branch": branch}))
            return ProcessOutput(actions=actions, transversal_status=status)
        if context.module_data.get("step") == 2:
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                success = module_utils.git_clone(context.github_project, context.module_data["branch"])
                if not success:
                    raise Exception(  # pylint: disable=broad-exception-raised
                        "Failed to clone the repository"
                    )

                status = context.transversal_status.setdefault(
                    f"{context.github_project.owner}/{context.github_project.repository}", {}
                ).setdefault("versions", {})
                status.setdefault("names", {})[context.module_data["branch"]] = {}
                _get_names(context, status.setdefault("names", {})[context.module_data["branch"]], branch)
                status.setdefault("dependencies", {})[context.module_data["branch"]] = {}
                _get_dependencies(
                    context, status.setdefault("dependencies", {})[context.module_data["branch"]]
                )
            return ProcessOutput(transversal_status=status)
        raise Exception("Invalid step")  # pylint: disable=broad-exception-raised

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext
    ) -> module.TransversalDashboardOutput:
        if "repository" in context.params:
            lexer = pygments.lexers.JsonLexer()
            formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")
            data = pygments.highlight(
                json.dumps(context.status.get(context.params["repository"], {}), indent=4), lexer, formatter
            )

            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/versions/repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
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
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            data = toml.load(file)
            name = data.get("project", {}).get("name")
            if name:
                names.setdefault("pypi", []).append(name)
            else:
                name = data.get("tool", {}).get("poetry", {}).get("name")
                if name:
                    names.setdefault("pypi", []).append(name)
    for filename in subprocess.run(  # nosec
        ["git", "ls-files", "setup.py", "*/setup.py"], check=True, capture_output=True, encoding="utf-8"
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            for line in file:
                match = re.match(r'^ *name ?= ?[\'"](.*)[\'"],?$', line)
                if match:
                    names.setdefault("pypi", []).append(match.group(1))

    if os.path.exists("ci/config.yaml"):
        with open("ci/config.yaml", encoding="utf-8") as file:
            data = yaml.load(file, Loader=yaml.SafeLoader)
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
    ).stdout.splitlines():
        with open(filename, encoding="utf-8") as file:
            data = json.load(file)
            name = data.get("name")
            if name:
                names.setdefault("npm", {}).setdefault(name, []).append(filename)

    names.setdefault("github", {}).setdefault(
        f"{context.github_project.owner}/{context.github_project.repository}", []
    ).append(branch)


def _get_dependencies(
    context: module.ProcessContext[dict[str, None]], result: dict[str, dict[str, Any]]
) -> None:
    proc = subprocess.run(  # nosec
        [
            "node",
            "renovate-graph",
            "--platform=local",
        ],
        env={
            "RG_LOCAL_PLATFORM": "github",
            "RG_LOCAL_ORGANISATION": context.github_project.owner,
            "RG_LOCAL_REPO": context.github_project.repository,
        },
        check=True,
        capture_output=True,
        encoding="utf-8",
    )

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
                result.setdefault(dep.get("datasource", "-"), {}).setdefault(
                    dep.get("depName", "-"), set()
                ).add(dep.get("currentValue"))

    for datasource_value in result.values():
        for dep, dep_value in datasource_value.items():
            datasource_value[dep] = list(dep_value)
