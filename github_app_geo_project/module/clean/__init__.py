"""Module to display the status of the workflows in the transversal dashboard."""

import json
import logging
import os.path
import subprocess  # nosec
import tempfile
from typing import Any, cast

import github
import requests
import tag_publish.configuration
import yaml
from pydantic import BaseModel

from github_app_geo_project import module
from github_app_geo_project.configuration import GithubProject
from github_app_geo_project.module import utils as module_utils

from . import configuration

_LOGGER = logging.getLogger(__name__)


class _ActionData(BaseModel):
    type: str
    name: str


class Clean(module.Module[configuration.CleanConfiguration, _ActionData, None]):
    """Module to display the status of the workflows in the transversal dashboard."""

    def title(self) -> str:
        """Get the title of the module."""
        return "Clean feature artifacts"

    def description(self) -> str:
        """Get the description of the module."""
        return "Clean feature branches or pull requests artifacts"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return "https://github.com/camptocamp/github-app-geo-project/wiki/Module-%E2%80%90-Clean"

    def get_github_application_permissions(self) -> module.GitHubApplicationPermissions:
        """Get the GitHub application permissions needed by the module."""
        return module.GitHubApplicationPermissions(
            {
                "contents": "write",
            },
            {"pull_request", "delete"},
        )

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        with open(os.path.join(os.path.dirname(__file__), "schema.json"), encoding="utf-8") as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("audit")  # type: ignore[no-any-return]

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_ActionData]]:
        """Get the action related to the module and the event."""
        if context.event_data.get("action") == "delete":
            return [
                module.Action(_ActionData(type="pull_request", name="123"), priority=module.PRIORITY_CRON)
            ]
        return []

    async def process(
        self, context: module.ProcessContext[configuration.CleanConfiguration, _ActionData, None]
    ) -> module.ProcessOutput[_ActionData, None]:
        """Process the action."""
        if context.module_config.get("docker", True):
            await self._clean_docker(context)
        for git in context.module_config.get("git", []):
            await self._clean_git(context, git)

        return module.ProcessOutput()

    async def _clean_docker(
        self, context: module.ProcessContext[configuration.CleanConfiguration, _ActionData, None]
    ) -> None:
        """Clean the Docker images on Docker Hub for the branch we delete."""
        # get the .github/publish.yaml

        try:
            publish_configuration_content = context.github_project.repo.get_contents(".github/publish.yaml")
            assert not isinstance(publish_configuration_content, list)
            publish_config = cast(
                tag_publish.configuration.Configuration,
                yaml.load(publish_configuration_content.decoded_content, Loader=yaml.SafeLoader),
            )
        except github.UnknownObjectException as exception:
            if exception.status != 404:
                raise
            return

        name = context.module_event_data.name
        if context.module_event_data.type == "pull_request":
            transformers = publish_config.get(
                "transformers",
                cast(tag_publish.configuration.Transformers, tag_publish.configuration.TRANSFORMERS_DEFAULT),
            )
            pull_match = tag_publish.match(
                name.split("/", 2)[2],
                tag_publish.compile_re(
                    transformers.get(
                        "pull_request_to_version", cast(tag_publish.configuration.Transform, [{}])
                    )
                ),
            )
            name = tag_publish.get_value(*pull_match)

        for repo in (
            publish_config.get("docker", {})
            .get(
                "repository",
                cast(
                    dict[str, tag_publish.configuration.DockerRepository],
                    tag_publish.configuration.DOCKER_REPOSITORY_DEFAULT,
                ),
            )
            .values()
        ):
            if context.module_event_data.type not in repo.get(
                "versions_type",
                tag_publish.configuration.DOCKER_REPOSITORY_VERSIONS_DEFAULT,
            ):
                continue
            host = repo.get("host", "docker.io")
            if host not in ["docker.io", "ghcr.io"]:
                _LOGGER.warning("Unsupported host %s", host)
                continue
            for image in publish_config.get("docker", {}).get("images", []):
                for tag in image.get("tags", []):
                    tag = tag.format(version=name)
                    _LOGGER.info("Cleaning %s/%s:%s", host, image["name"], tag)

                    if host == "docker.io":
                        self._clean_docker_hub_image(image["name"], tag)
                    else:
                        self._clean_ghcr_image(image["name"], tag, context.github_project)

    def _clean_docker_hub_image(self, image: str, tag: str) -> None:
        username = os.environ["DOCKERHUB_USERNAME"]
        password = os.environ["DOCKERHUB_PASSWORD"]
        token = requests.post(
            "https://hub.docker.com/v2/users/login/",
            headers={"Content-Type": "application/json"},
            data=json.dumps(
                {
                    "username": username,
                    "password": password,
                }
            ),
            timeout=int(os.environ.get("GHCI_REQUESTS_TIMEOUT", "30")),
        ).json()["token"]

        response = requests.head(
            f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}/",
            headers={"Authorization": "JWT " + token},
            timeout=int(os.environ.get("C2CCIUTILS_TIMEOUT", "30")),
        )
        if response.status_code == 404:
            return
        if not response.ok:
            _LOGGER.error("Error checking image: docker.io/%s:%s", image, tag)

        response = requests.delete(
            f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}/",
            headers={"Authorization": "JWT " + token},
            timeout=int(os.environ.get("C2CCIUTILS_TIMEOUT", "30")),
        )
        if not response.ok:
            _LOGGER.error("Error on deleting image: docker.io/%s:%s", image, tag)

    def _clean_ghcr_image(self, image: str, tag: str, github_project: GithubProject) -> None:
        image_split = image.split("/", 1)
        response = requests.delete(
            f"https://api.github.com/orgs/{image_split[0]}/packages/container/{image_split[1]}/versions/{tag}",
            headers={
                "Authorization": "Bearer " + github_project.token,
                "Accept": "application/vnd.github.v3+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if not response.ok:
            _LOGGER.error("Error on deleting image: ghcr.io/%s:%s", image, tag)

    async def _clean_git(
        self,
        context: module.ProcessContext[configuration.CleanConfiguration, _ActionData, None],
        git: configuration.Git,
    ) -> None:
        """Clean the Git repository for the branch we delete."""
        if git.get("on-type", configuration.ON_TYPE_DEFAULT) not in (context.module_event_data.type, "all"):
            return

        branch = git.get("branch", configuration.BRANCH_DEFAULT)
        folder = git.get("folder", configuration.FOLDER_DEFAULT).format(name=context.module_event_data.name)

        async with module_utils.WORKING_DIRECTORY_LOCK:
            # Checkout the right branch on a temporary directory
            with tempfile.TemporaryDirectory() as tmpdirname:
                os.chdir(tmpdirname)
                _LOGGER.debug("Clone the repository in the temporary directory: %s", tmpdirname)
                success = module_utils.git_clone(context.github_project, branch)
                if not success:
                    _LOGGER.error(
                        "Error on cloning the repository %s/%s",
                        context.github_project.owner,
                        context.github_project.repository,
                    )

                os.chdir(context.github_project.repository)
                subprocess.run(["git", "rm", folder], check=True)
                subprocess.run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"Delete {folder} to clean {context.module_event_data.type} {context.module_event_data.name}",
                    ],
                    check=True,
                )
                subprocess.run(["git", "push", "origin", branch], check=True)
