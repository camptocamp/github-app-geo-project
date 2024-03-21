"""Utility functions for the auto* modules."""

import json
import logging
import os
import subprocess  # nosec
import tempfile
from typing import Any, cast

import c2cciutils.security
import github
import yaml

from github_app_geo_project import module
from github_app_geo_project.module import utils
from github_app_geo_project.module.audit import configuration
from github_app_geo_project.module.audit import utils as audit_utils

_LOGGER = logging.getLogger(__name__)

_OUTDATED = "Outdated version"


def _parse_issue_data(issue_data: str) -> dict[str, list[str]]:
    """Parse the issue data."""
    result: dict[str, list[str]] = {}
    key = "undefined"
    for line in issue_data.split("\n"):
        if line.startswith("### "):
            key = line[4:]
        elif line:
            result.get(key, []).append(line)
    return result


def _format_issue_data(issue_data: dict[str, list[str]]) -> str:
    """Format the issue data."""
    result = ""
    for key, value in issue_data.items():
        if value:
            result += f"## {key}\n"
            result += "\n".join(value)
            result += "\n"
    return result


def _get_versions(security: c2cciutils.security.Security) -> list[str]:
    alternate_index = security.header("Alternate Tag")
    version_index = security.header("Version")
    support_until_index = security.header("Support Until")
    alternate = []
    if alternate_index >= 0:
        for row in security.data:
            if row[alternate_index]:
                alternate.append(row[alternate_index])

    versions = []
    for row in security.data:
        if row[support_until_index] != "Unsupported":
            if alternate:
                if row[alternate_index] not in alternate:
                    versions.append(row[version_index])
            else:
                versions.append(row[version_index])
    return versions


class Audit(module.Module[configuration.AuditConfiguration]):
    """The auto module."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Audit (Snyk/DPKG)"

    def description(self) -> str:
        """Get the description of the module."""
        return "Audit the project with Snyk (for CVE in dependency) and update DPKG package version to trigger a rebuild"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Audit"

    def required_issue_dashboard(self) -> bool:
        """Check if the module requires an issue dashboard."""
        return True

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        if "SECURITY.md" in context.event_data.get("push", {}).get("files", []):
            return [module.Action(priority=module.PRIORITY_CRON, data={"type": "outdated"})]
        if context.event_data.get("event") == "daily":
            repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
            security_file = repo.get_contents("SECURITY.md")
            assert isinstance(security_file, github.ContentFile.ContentFile)
            security = c2cciutils.security.Security(security_file.decoded_content)

            versions = _get_versions(security)

            results = [
                {"type": "outdated"},
            ]
            for version in versions:
                results += [
                    {"type": "snyk", "version": version},
                    {"type": "dpkg", "version": version},
                ]
            return [module.Action(priority=module.PRIORITY_CRON, data=d) for d in results]

        return []

    def process(
        self, context: module.ProcessContext[configuration.AuditConfiguration]
    ) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        issue_data = _parse_issue_data(context.issue_data)
        if context.module_data["type"] == "outdated":
            repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
            security_file = repo.get_contents("SECURITY.md")
            assert isinstance(security_file, github.ContentFile.ContentFile)
            security = c2cciutils.security.Security(security_file.decoded_content)

            issue_data[_OUTDATED] = audit_utils.outdated_versions(security)

            # Remove outdated version in the dashdoard
            versions = _get_versions(security)
            keys = [key for key in issue_data if key != _OUTDATED]
            for key in keys:
                to_delete = True
                for version in versions:
                    if key.endswith(f" {version}"):
                        to_delete = False
                        break
                if to_delete:
                    del issue_data[key]
            return self._get_process_output(context, issue_data)

        key = f"Undefined {context.module_data['version']}"
        new_branch = f"ghci/audit/{context.module_data['type']}/{context.module_data['version']}"
        if context.module_data["type"] == "snyk":
            key = f"Snyk check/fix {context.module_data['version']}"
        if context.module_data["type"] == "dpkg":
            key = f"DPKG {context.module_data['version']}"
        issue_data[key] = []
        try:
            branch: str = cast(context.module_data["version"], str)  # type: ignore[name-defined]
            if os.path.exists("ci/config.yaml"):
                with open("ci/config.yaml", encoding="utf-8") as file:
                    ci_config = yaml.load(file, Loader=yaml.SafeLoader).get("audit", {})
                if "branch_to_version_re" in ci_config.get("version", {}):
                    branch_to_version_re = c2cciutils.compile_re(ci_config["version"]["branch-to-version-re"])

                    repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
                    for github_branch in repo.get_branches():
                        matched, conf, value = c2cciutils.match(github_branch.name, branch_to_version_re)
                        version = c2cciutils.substitute(matched, conf, value)
                        if version == branch:
                            branch = github_branch.name
                            break

            assert (
                context.github.application.__requester.__auth is not None  # pylint: disable=protected-access
            )
            token = context.github.application.__requester.__auth.token  # pylint: disable=protected-access
            # Checkout the right branch on a temporary directory
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                    [
                        "git",
                        "clone",
                        "--depth=1",
                        f"--branch={branch}",
                        f"https://x-access-token:{token}@github.com/sbrunner/test-github-app.git",
                    ],
                    capture_output=True,
                    encoding="utf-8",
                )
                if proc.returncode != 0:
                    issue_data[key].append(utils.ansi_proc_dashboard("Error while cloning the project", proc))

            if context.module_data["type"] == "snyk":
                python_version = ""
                if os.path.exists(".tool-versions"):
                    with open(".tool-versions", encoding="utf-8") as file:
                        for line in file:
                            if line.startswith("python "):
                                python_version = ".".join(line.split(" ")[1].split(".")[0:2])
                                break
                if python_version:
                    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                        ["pipenv", "local", python_version], capture_output=True, encoding="utf-8"
                    )
                    if proc.returncode != 0:
                        issue_data[key].append(
                            utils.ansi_proc_dashboard("Error while setting the python version", proc)
                        )

            if context.module_data["type"] == "snyk":
                local_config: configuration.AuditConfiguration = {}
                if os.path.exists(".github/ghci.yaml"):
                    with open(".github/ghci.yaml", encoding="utf-8") as file:
                        local_config = yaml.load(file, Loader=yaml.SafeLoader).get("audit", {})
                result, body, create_issue = audit_utils.snyk(branch, context.module_config, local_config)
                if create_issue or result:
                    repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
                    issue = repo.create_issue(
                        title=f"Error on running Snyk on {branch}",
                        body=body or "\n".join(result),
                    )
                    issue_data[key] += [f"Error on running Snyk on {branch}: #{issue.number}", ""]
                issue_data[key] += result

            if context.module_data["type"] == "dpkg":
                body = "Update dpkg packages"
                issue_data[key] += audit_utils.dpkg()
                if issue_data[key]:
                    repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
                    issue = repo.create_issue(
                        title=f"Error on running DPKG on {branch}",
                        body="\n".join(issue_data[key]),
                    )
                    issue_data[key] = [
                        f"Error on running DPKG on {branch}: #{issue.number}",
                        "",
                        *issue_data[key],
                    ]

            diff_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                ["git", "diff", "--quiet"]
            )
            if diff_proc.returncode != 0:
                proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                    ["git", "checkout", "-b", new_branch], capture_output=True, encoding="utf-8"
                )
                if proc.returncode != 0:
                    issue_data[key].append(
                        utils.ansi_proc_dashboard("Error while creating the new branch", proc)
                    )
                    return self._get_process_output(context, issue_data)

                repo = context.github.application.get_repo(f"{context.owner}/{context.repository}")
                error, pull_request = utils.create_commit_pull_request(
                    branch, new_branch, f"Audit {key}", body, repo
                )
                if error is not None:
                    issue_data[key].append(error)
                    return self._get_process_output(context, issue_data)
                if pull_request is not None:
                    issue_data[key] = [f"Pull request created: {pull_request.html_url}", "", *issue_data[key]]
        except Exception as exception:  # pylint: disable=broad-except
            _LOGGER.exception("Audit %s error", key)
            issue_data[key].append(f"Error: {exception}")

        return self._get_process_output(context, issue_data)

    def _get_process_output(
        self,
        context: module.ProcessContext[configuration.AuditConfiguration],
        issue_data: dict[str, list[str]],
    ) -> module.ProcessOutput:
        module_status = context.transversal_status
        if issue_data:
            module_status[f"{context.owner}/{context.repository}"] = issue_data
        else:
            del module_status[f"{context.owner}/{context.repository}"]

        return module.ProcessOutput(
            dashboard=_format_issue_data(issue_data), transversal_status=module_status
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
            return json.loads(schema_file.read()).get("definitions", {}).get("auto")  # type: ignore[no-any-return]

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the permissions and events required by the module."""
        return module.GitHubApplicationPermissions(
            {
                "pull_requests": "write",
                "issues": "write",
                "contents": "write",
                "workflows": "write",
            },
            {"push"},
        )

    def has_transversal_dashboard(self) -> bool:
        """Say that the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext
    ) -> module.TransversalDashboardOutput:
        """Get the transversal dashboard content."""
        if "repository" in context.params:
            return module.TransversalDashboardOutput(
                renderer="github_app_geo_project:module/audit/repository.html",
                # template="repository.html",
                data={
                    "title": self.title() + " - " + context.params["repository"],
                    "audit": context.status.get(context.params["repository"], {}),
                },
            )

        result = []
        for repository, data in context.status.items():
            if data:
                result.append(
                    {
                        "repository": repository,
                        "data": data.keys(),
                    }
                )

        return module.TransversalDashboardOutput(
            # template="dashboard.html",
            renderer="github_app_geo_project:module/audit/dashboard.html",
            data={"repositories": result},
        )
