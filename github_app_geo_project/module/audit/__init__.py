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
from github_app_geo_project.module import PRIORITY_HIGH, ProcessOutput
from github_app_geo_project.module import utils as module_utils
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
    alternate_index = security.headers.index("Alternate Tag") if "Alternate Tag" in security.headers else -1
    version_index = security.headers.index("Version") if "Version" in security.headers else -1
    supported_until_index = (
        security.headers.index("Supported Until") if "Supported Until" in security.headers else -1
    )

    if version_index < 0:
        _LOGGER.error("No Version column in the SECURITY.md")
        return []
    if supported_until_index < 0:
        _LOGGER.error("No Supported Until column in the SECURITY.md")
        return []

    alternate = []
    if alternate_index >= 0:
        for row in security.data:
            if row[alternate_index]:
                alternate.append(row[alternate_index])

    versions = []
    for row in security.data:
        if row[supported_until_index] != "Unsupported":
            if alternate:
                if row[alternate_index] not in alternate:
                    versions.append(row[version_index])
            else:
                versions.append(row[version_index])
    return versions


def _get_process_output(
    context: module.ProcessContext[configuration.AuditConfiguration],
    issue_check: module_utils.DashboardIssue,
    issue_data: dict[str, list[str]],
) -> module.ProcessOutput:
    issue_check.set_check(context.module_data["type"], False)

    module_status = context.transversal_status
    if issue_data:
        module_status[f"{context.github_project.owner}/{context.github_project.repository}"] = issue_data
    else:
        if f"{context.github_project.owner}/{context.github_project.repository}" in module_status:
            del module_status[f"{context.github_project.owner}/{context.github_project.repository}"]

    return module.ProcessOutput(
        dashboard="\n<!---->\n".join([issue_check.to_string(), _format_issue_data(issue_data)]),
        transversal_status=module_status,
    )


def _process_outdated(
    context: module.ProcessContext[configuration.AuditConfiguration], issue_data: dict[str, list[str]]
) -> None:
    repo = context.github_project.github.get_repo(
        f"{context.github_project.owner}/{context.github_project.repository}"
    )
    versions: list[str] = []
    try:
        security_file = repo.get_contents("SECURITY.md")
        assert isinstance(security_file, github.ContentFile.ContentFile)
        security = c2cciutils.security.Security(security_file.decoded_content.decode("utf-8"))

        issue_data[_OUTDATED] = audit_utils.outdated_versions(security)
        # Remove outdated version in the dashboard
        versions = _get_versions(security)
    except github.GithubException as exception:
        if exception.status == 404:
            issue_data[_OUTDATED] = ["No SECURITY.md file in the repository"]
            _LOGGER.debug("No SECURITY.md file in the repository")
        else:
            issue_data[_OUTDATED] = [f"Error while getting SECURITY.md: {exception}"]
            raise

    keys = [key for key in issue_data if key != _OUTDATED]
    for key in keys:
        to_delete = True
        for version in versions:
            if key.endswith(f" {version}"):
                to_delete = False
                break
        if to_delete:
            del issue_data[key]


def _process_snyk_dpkg(
    context: module.ProcessContext[configuration.AuditConfiguration], issue_data: dict[str, list[str]]
) -> None:
    key = f"Undefined {context.module_data['version']}"
    new_branch = f"ghci/audit/{context.module_data['type']}/{context.module_data['version']}"
    if context.module_data["type"] == "snyk":
        key = f"Snyk check/fix {context.module_data['version']}"
    if context.module_data["type"] == "dpkg":
        key = f"DPKG {context.module_data['version']}"
    issue_data[key] = []
    try:
        branch: str = cast(str, context.module_data["version"])
        if os.path.exists("ci/config.yaml"):
            with open("ci/config.yaml", encoding="utf-8") as file:
                ci_config = yaml.load(file, Loader=yaml.SafeLoader).get("audit", {})
            if "branch_to_version_re" in ci_config.get("version", {}):
                branch_to_version_re = c2cciutils.compile_re(ci_config["version"]["branch-to-version-re"])

                repo = context.github_project.github.get_repo(
                    f"{context.github_project.owner}/{context.github_project.repository}"
                )
                for github_branch in repo.get_branches():
                    matched, conf, value = c2cciutils.match(github_branch.name, branch_to_version_re)
                    version = c2cciutils.substitute(matched, conf, value)
                    if version == branch:
                        branch = github_branch.name
                        break

        token = context.github_project.token
        # Checkout the right branch on a temporary directory
        with tempfile.TemporaryDirectory() as tmpdirname:
            os.chdir(tmpdirname)
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "git",
                    "clone",
                    "--depth=1",
                    f"--branch={branch}",
                    f"https://x-access-token:{token}@github.com/"
                    f"{context.github_project.owner}/{context.github_project.repository}.git",
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                message = module_utils.ansi_proc_dashboard(proc)
                _LOGGER.error(message)
                issue_data[key].append(
                    f"<details><summary>Error while cloning the project</summary>{message}</details>"
                )
                return

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
                    message = module_utils.ansi_proc_dashboard(proc)
                    _LOGGER.error(message)
                    issue_data[key].append(
                        f"<details><summary>Error while setting the python version</summary>{message}</details>"
                    )

            local_config: configuration.AuditConfiguration = {}
            if os.path.exists(".github/ghci.yaml"):
                with open(".github/ghci.yaml", encoding="utf-8") as file:
                    local_config = yaml.load(file, Loader=yaml.SafeLoader).get("audit", {})
            result, body, create_issue = audit_utils.snyk(branch, context.module_config, local_config)
            if create_issue or result:
                repo = context.github_project.github.get_repo(
                    f"{context.github_project.owner}/{context.github_project.repository}"
                )
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
                repo = context.github_project.github.get_repo(
                    f"{context.github_project.owner}/{context.github_project.repository}"
                )
                issue = repo.create_issue(
                    title=f"Error on running dpkg on {branch}",
                    body="\n".join(issue_data[key]),
                )
                issue_data[key] = [
                    f"Error on running dpkg on {branch}: #{issue.number}",
                    "",
                    *issue_data[key],
                ]

        diff_proc = subprocess.run(["git", "diff", "--quiet"])  # nosec # pylint: disable=subprocess-run-check
        if diff_proc.returncode != 0:
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                ["git", "checkout", "-b", new_branch], capture_output=True, encoding="utf-8"
            )
            if proc.returncode != 0:
                message = module_utils.ansi_proc_dashboard(proc)
                _LOGGER.error(message)
                issue_data[key].append(
                    f"<details><summary>Error while creating the new branch</summary>{message}</details>"
                )

                return

            repo = context.github_project.github.get_repo(
                f"{context.github_project.owner}/{context.github_project.repository}"
            )
            error, pull_request = module_utils.create_commit_pull_request(
                branch, new_branch, f"Audit {key}", body, repo
            )
            if error is not None:
                _LOGGER.error(error)
                issue_data[key].append(
                    f"<details><summary>Error while create commit or pull request</summary>{error}</details>"
                )
                return

            if pull_request is not None:
                issue_data[key] = [f"Pull request created: {pull_request.html_url}", "", *issue_data[key]]

    except Exception as exception:  # pylint: disable=broad-except
        _LOGGER.exception("Audit %s error", key)
        issue_data[key].append(f"Error: {exception}")


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
        _LOGGER.debug("Event data: %s", context.event_data)
        results: list[module.Action] = []
        snyk = False
        dpkg = False
        is_dashboard = context.event_data.get("type") == "dashboard"
        if is_dashboard:
            old_check = module_utils.DashboardIssue(
                context.event_data.get("old_data", "").split("<!---->")[0]
            )
            new_check = module_utils.DashboardIssue(
                context.event_data.get("new_data", "").split("<!---->")[0]
            )

            if not old_check.is_checked("outdated") and new_check.is_checked("outdated"):
                results.append(module.Action(priority=module.PRIORITY_STANDARD, data={"type": "outdated"}))
            if not old_check.is_checked("snyk") and new_check.is_checked("snyk"):
                snyk = True
            if not old_check.is_checked("dpkg") and new_check.is_checked("dpkg"):
                dpkg = True

        if context.event_data.get("type") == "event" and context.event_data.get("name") == "daily":
            results.append(module.Action(priority=module.PRIORITY_CRON, data={"type": "outdated"}))
            snyk = True
            dpkg = True

        results.append(
            module.Action(
                priority=module.PRIORITY_HIGH, data={"snyk": snyk, "dpkg": dpkg, "is_dashboard": is_dashboard}
            )
        )
        return results

    def process(
        self, context: module.ProcessContext[configuration.AuditConfiguration]
    ) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        issue_data_splitted = context.issue_data.split("<!---->")
        if len(issue_data_splitted) == 1:
            issue_data_splitted.append("")
        issue_check = module_utils.DashboardIssue(issue_data_splitted[0])
        issue_data = _parse_issue_data(issue_data_splitted[1])

        issue_check.add_check("outdated", "Check outdated version", False)
        issue_check.add_check("snyk", "Check security vulnerabilities with Snyk", False)
        issue_check.add_check("dpkg", "Update dpkg packages", False)

        if context.module_data["type"] == "outdated":
            _get_process_output(context, issue_check, issue_data)

        else:
            if "version" not in context.module_data:
                # Creates new jobs with the versions from the SECURITY.md
                repo = context.github_project.github.get_repo(
                    f"{context.github_project.owner}/{context.github_project.repository}"
                )
                versions = []
                try:
                    security_file = repo.get_contents("SECURITY.md")
                    assert isinstance(security_file, github.ContentFile.ContentFile)
                    security = c2cciutils.security.Security(security_file.decoded_content.decode("utf-8"))

                    versions = _get_versions(security)
                except github.GithubException as exception:
                    if exception.status == 404:
                        _LOGGER.debug("No SECURITY.md file in the repository")
                    else:
                        raise
                _LOGGER.debug("Versions: %s", versions)

                results = []
                for version in versions:
                    if context.module_data.get("snyk", False):
                        results.append({"type": "snyk", "version": version})
                    if context.module_data.get("dpkg", False):
                        results.append({"type": "dpkg", "version": version})
                return ProcessOutput(
                    actions=[
                        module.Action(
                            priority=module.PRIORITY_STANDARD
                            if context.module_data["is_dashboard"]
                            else module.PRIORITY_CRON,
                            data=d,
                        )
                        for d in results
                    ],
                )
            else:
                _process_snyk_dpkg(context, issue_data)

        return _get_process_output(context, issue_check, issue_data)

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
