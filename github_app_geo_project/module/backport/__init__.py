"""Module to display the status of the workflows in the transversal dashboard."""

import json
import logging
import os.path
import subprocess  # nosec
import tempfile
from typing import Any

import github
import security_md
from pydantic import BaseModel

from github_app_geo_project import module
from github_app_geo_project.module import utils as module_utils

from . import configuration

_LOGGER = logging.getLogger(__name__)


class _ActionData(BaseModel):
    type: str
    pull_request_number: int | None = None
    branch: str | None = None


class Backport(module.Module[configuration.BackportConfiguration, _ActionData, None]):
    """Module used to backport a pull request to an other branch."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Backport pull request"

    def description(self) -> str:
        """Get the description of the module."""
        return "Backport a pull request to an other branch"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Backport"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "write",
            },
            {"pull_request", "push"},
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("backport")  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_ActionData]]:
        """Get the action related to the module and the event."""
        # SECURITY.md update
        security_md_update = False
        for commit in context.event_data.get("commits", []):
            for file in {*commit.get("added", []), *commit.get("modified", []), *commit.get("removed", [])}:
                if file == "SECURITY.md":
                    security_md_update = True
                    break
            if security_md_update:
                break
        if security_md_update:
            return [module.Action(_ActionData(type="SECURITY.md"), priority=module.PRIORITY_CRON)]

        if context.event_data.get("action") == "closed" and "pull_request" in context.event_data:
            return [module.Action(_ActionData(type="pull_request"), priority=module.PRIORITY_STANDARD)]
        if context.event_data.get("action") == "labeled" and "pull_request" in context.event_data:
            return [module.Action(_ActionData(type="pull_request"), priority=module.PRIORITY_STANDARD)]
        return []

    async def process(
        self, context: module.ProcessContext[configuration.BackportConfiguration, _ActionData, None]
    ) -> module.ProcessOutput[_ActionData, None]:
        """Process the action."""
        if context.module_event_data.type == "SECURITY.md":
            repo = context.github_project.repo
            if context.event_data.get("ref") == f"refs/heads/{repo.default_branch}":
                try:
                    security_file = repo.get_contents("SECURITY.md")
                    assert isinstance(security_file, github.ContentFile.ContentFile)
                    security = security_md.Security(security_file.decoded_content.decode("utf-8"))
                    branches = {*security.branches()}
                except github.GithubException as exception:
                    if exception.status == 404:
                        _LOGGER.debug("No SECURITY.md file in the repository")
                        branches = set()

                    else:
                        _LOGGER.exception("Error while getting SECURITY.md")
                        raise

                if branches:
                    branches.add(repo.default_branch)

                labels_config = context.module_config.get("labels", {})
                if labels_config.get("auto-delete", configuration.AUTO_DELETE_DEFAULT):
                    for label in repo.get_labels():
                        if label.name.startswith("backport "):
                            branch = label.name[len("backport ") :]
                            if branch not in branches:
                                label.delete()

                if labels_config.get("auto-create", configuration.AUTO_CREATE_DEFAULT):
                    for branch in branches:
                        if not repo.get_label(f"backport {branch}"):
                            repo.create_label(
                                f"backport {branch}",
                                labels_config.get("color", configuration.COLOR_DEFAULT),
                            )

            return module.ProcessOutput()

        if context.module_event_data.type == "pull_request":
            pull_request = context.github_project.repo.get_pull(context.event_data["pull_request"]["number"])
            if pull_request.state == "closed" and pull_request.merged:
                branches = set()
                for label in pull_request.labels:
                    if label.name.startswith("backport "):
                        branches.add(label.name[len("backport ") :])

                return module.ProcessOutput(
                    actions=[
                        module.Action(
                            _ActionData(
                                type="backport", pull_request_number=pull_request.number, branch=branch
                            ),
                            priority=module.PRIORITY_STANDARD,
                        )
                        for branch in branches
                    ]
                )
            return module.ProcessOutput()

        if context.module_event_data.type == "backport":
            assert context.module_event_data.pull_request_number is not None
            pull_request = context.github_project.repo.get_pull(context.module_event_data.pull_request_number)
            assert context.module_event_data.branch is not None
            await self._backport(context, pull_request, context.module_event_data.branch)
            return module.ProcessOutput()

        return module.ProcessOutput()

    async def _backport(
        self,
        context: module.ProcessContext[configuration.BackportConfiguration, _ActionData, None],
        pull_request: github.PullRequest.PullRequest,
        target_branch: str,
    ) -> None:
        """Backport the pull request to the target branch."""
        backport_branch = f"backport/{pull_request.number}-to-{target_branch}"
        try:
            if context.github_project.repo.get_branch(backport_branch):
                _LOGGER.debug("Branch %s already exists", backport_branch)
                return
        except github.GithubException as exception:
            if exception.status != 404:
                _LOGGER.exception("Error while getting branch %s", backport_branch)
                raise

        async with module_utils.WORKING_DIRECTORY_LOCK:
            # Checkout the right branch on a temporary directory
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                _LOGGER.debug("Clone the repository in the temporary directory: %s", tmpdirname)
                success = module_utils.git_clone(context.github_project, target_branch)
                if not success:
                    _LOGGER.error(
                        "Error on cloning the repository %s/%s",
                        context.github_project.owner,
                        context.github_project.repository,
                    )

                os.chdir(context.github_project.repository)

                # Checkout the branch
                subprocess.run(["git", "checkout", "-b", backport_branch], check=True)

                failed_commits: list[str] = []
                # For all commits in the pull request
                for commit in pull_request.get_commits():
                    # Cherry-pick the commit
                    if failed_commits:
                        failed_commits.append(commit.sha)
                    else:
                        try:
                            subprocess.run(["git", "cherry-pick", commit.sha], check=True)
                        except subprocess.CalledProcessError:
                            failed_commits.append(commit.sha)

                message = [f"Backport of #{pull_request.number} to {target_branch}"]
                if failed_commits:
                    message.extend(
                        [
                            "",
                            f"Error on cherry picking:\n{failed_commits}",
                            "",
                            "To continue do:",
                            "git fetch \\" f"  && git checkout {backport_branch} \\",
                            f"  && git reset --hard HEAD^ \\"
                            f"  && git cherry-pick {' '.join(failed_commits)}",
                            f"git push origin {backport_branch} --force",
                        ]
                    )
                    with open("BACKPORT_TODO", "w", encoding="utf-8") as f:
                        f.write("\n".join(message))
                    subprocess.run(["git", "add", "BACKPORT_TODO"], check=True)
                    subprocess.run(
                        ["git", "commit", "--message=[skip ci] Add instructions to finish the backport"],
                        check=True,
                    )
                await module_utils.create_pull_request(
                    target_branch,
                    backport_branch,
                    f"[Backport {target_branch}] {pull_request.title}",
                    "\n".join(message),
                    project=context.github_project,
                )
