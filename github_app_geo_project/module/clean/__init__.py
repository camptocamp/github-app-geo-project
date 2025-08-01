"""Module to display the status of the workflows in the transversal dashboard."""

import asyncio
import base64
import json
import logging
import os
import subprocess  # nosec
import tempfile
from pathlib import Path
from typing import Any, cast

import aiohttp
import githubkit.exception
import githubkit.versions
import githubkit.versions.latest
import githubkit.versions.latest.models
import githubkit.webhooks
import tag_publish.configuration
import yaml
from pydantic import BaseModel

from github_app_geo_project import module, utils
from github_app_geo_project.configuration import GithubProject
from github_app_geo_project.module import utils as module_utils

from . import configuration

_LOGGER = logging.getLogger(__name__)


class CleanError(Exception):
    """Error raised when an error occurs during the clean process."""


class _ActionData(BaseModel):
    type: str
    names: list[str]


class Clean(module.Module[configuration.CleanConfiguration, _ActionData, None, None]):
    """Module used to clean the related artifacts on deleting a feature branch or on closing a pull request."""

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

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema for the module."""
        with (Path(__file__).parent / "schema.json").open(
            encoding="utf-8",
        ) as schema_file:
            return json.loads(schema_file.read()).get("properties", {}).get("clean")  # type: ignore[no-any-return]

    def get_actions(
        self,
        context: module.GetActionContext,
    ) -> list[module.Action[_ActionData]]:
        """Get the action related to the module and the event."""
        if context.module_event_name == "pull_request":
            event_data_pull_request = githubkit.webhooks.parse_obj(
                "pull_request",
                context.github_event_data,
            )
            if event_data_pull_request.action == "closed":
                return [
                    module.Action(
                        _ActionData(
                            type="pull_request",
                            names=[
                                str(event_data_pull_request.number),
                                event_data_pull_request.pull_request.head.ref,
                            ],
                        ),
                        priority=module.PRIORITY_CRON,
                    ),
                ]
        if context.module_event_name == "delete":
            event_data_delete = githubkit.webhooks.parse_obj(
                "delete",
                context.github_event_data,
            )
            if event_data_delete.ref_type == "branch":
                return [
                    module.Action(
                        _ActionData(type="branch", names=[event_data_delete.ref]),
                        priority=module.PRIORITY_CRON,
                    ),
                ]
        return []

    async def process(
        self,
        context: module.ProcessContext[configuration.CleanConfiguration, _ActionData],
    ) -> module.ProcessOutput[_ActionData, None]:
        """Process the action."""
        if context.module_config.get("docker", True):
            await self._clean_docker(context)
        for git in context.module_config.get("git", []):
            await self._clean_git(context, git)

        return module.ProcessOutput()

    async def _clean_docker(
        self,
        context: module.ProcessContext[configuration.CleanConfiguration, _ActionData],
    ) -> None:
        """Clean the Docker images on Docker Hub for the branch we delete."""
        try:
            publish_configuration_content = (
                await context.github_project.aio_github.rest.repos.async_get_content(
                    owner=context.github_project.owner,
                    repo=context.github_project.repository,
                    path=".github/publish.yaml",
                )
            ).parsed_data
            if isinstance(
                publish_configuration_content,
                githubkit.versions.latest.models.ContentFile,
            ) and isinstance(publish_configuration_content.content, str):
                publish_config = cast(
                    "tag_publish.configuration.Configuration",
                    yaml.load(
                        base64.b64decode(publish_configuration_content.content).decode(
                            "utf-8",
                        ),
                        Loader=yaml.SafeLoader,
                    ),
                )
                message = module_utils.HtmlMessage(utils.format_json(publish_config))  # type: ignore[arg-type]
                message.title = "Used publish configuration"
                _LOGGER.info(message)
            else:
                publish_config = {}
        except githubkit.exception.RequestFailed as exception:
            if exception.response.status_code != 404:
                raise
            return

        for name in context.module_event_data.names:
            if context.module_event_data.type == "pull_request":
                transformers = publish_config.get(
                    "transformers",
                    cast(
                        "tag_publish.configuration.Transformers",
                        tag_publish.configuration.TRANSFORMERS_DEFAULT,
                    ),
                )
                pull_match = tag_publish.match(
                    name,
                    tag_publish.compile_re(
                        transformers.get(
                            "pull_request_to_version",
                            cast("tag_publish.configuration.Transform", [{}]),
                        ),
                    ),
                )
                name = tag_publish.get_value(*pull_match)  # noqa: PLW2901

            for repo in (
                publish_config.get("docker", {})
                .get(
                    "repository",
                    cast(
                        "dict[str, tag_publish.configuration.DockerRepository]",
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
                        tag = tag.format(version=name)  # noqa: PLW2901
                        _LOGGER.info("Cleaning %s/%s:%s", host, image["name"], tag)

                        if host == "docker.io":
                            await self._clean_docker_hub_image(image["name"], tag)
                        else:
                            await self._clean_ghcr_image(
                                image["name"],
                                tag,
                                context.github_project,
                            )

    async def _clean_docker_hub_image(self, image: str, tag: str) -> None:
        async with aiohttp.ClientSession() as session:
            username = os.environ["DOCKERHUB_USERNAME"]
            password = os.environ["DOCKERHUB_PASSWORD"]
            async with (
                asyncio.timeout(int(os.environ.get("C2CCIUTILS_TIMEOUT", "30"))),
                session.post(
                    "https://hub.docker.com/v2/users/login/",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(
                        {
                            "username": username,
                            "password": password,
                        },
                    ),
                ) as response,
            ):
                token = (await response.json())["token"]

            async with (
                asyncio.timeout(int(os.environ.get("C2CCIUTILS_TIMEOUT", "30"))),
                session.head(
                    f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}/",
                    headers={"Authorization": "JWT " + token},
                ) as response,
            ):
                if response.status == 404:
                    return
                if not response.ok:
                    _LOGGER.error("Error checking image: docker.io/%s:%s", image, tag)

            async with (
                asyncio.timeout(int(os.environ.get("C2CCIUTILS_TIMEOUT", "30"))),
                session.delete(
                    f"https://hub.docker.com/v2/repositories/{image}/tags/{tag}/",
                    headers={"Authorization": "JWT " + token},
                ) as response,
            ):
                if not response.ok:
                    _LOGGER.error(
                        "Error on deleting image: docker.io/%s:%s",
                        image,
                        tag,
                    )

    async def _clean_ghcr_image(
        self,
        image: str,
        tag: str,
        github_project: GithubProject,
    ) -> None:
        image_split = image.split("/", 1)
        async with (
            aiohttp.ClientSession() as session,
            session.delete(
                f"https://api.github.com/orgs/{image_split[0]}/packages/container/{image_split[1]}/versions/{tag}",
                headers={
                    "Authorization": "Bearer " + github_project.token,
                    "Accept": "application/vnd.github.v3+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ) as response,
        ):
            if not response.ok:
                _LOGGER.error("Error on deleting image: ghcr.io/%s:%s", image, tag)

    async def _clean_git(
        self,
        context: module.ProcessContext[configuration.CleanConfiguration, _ActionData],
        git: configuration.Git,
    ) -> None:
        """Clean the Git repository for the branch we delete."""
        if git.get("on-type", configuration.ON_TYPE_DEFAULT) not in (
            context.module_event_data.type,
            "all",
        ):
            return

        branch = git.get("branch", configuration.BRANCH_DEFAULT)
        for name in context.module_event_data.names:
            folder = git.get("folder", configuration.FOLDER_DEFAULT).format(name=name)

            # Checkout the right branch on a temporary directory
            with tempfile.TemporaryDirectory() as tmpdirname:
                cwd = Path(tmpdirname)
                _LOGGER.debug(
                    "Clone the repository in the temporary directory: %s",
                    tmpdirname,
                )
                new_cwd = await module_utils.git_clone(
                    context.github_project,
                    branch,
                    cwd,
                )
                if new_cwd is None:
                    _LOGGER.error(
                        "Error on cloning the repository %s/%s",
                        context.github_project.owner,
                        context.github_project.repository,
                    )
                    exception_message = "Failed to clone the repository"
                    raise CleanError(exception_message)

                cwd = new_cwd

                if not (cwd / folder).exists():
                    _LOGGER.info(
                        "The folder '%s' does not exist in the branch '%s', nothing to do",
                        folder,
                        branch,
                    )
                    continue

                _LOGGER.info(
                    "Cleaning the folder '%s' in the branch '%s'",
                    folder,
                    branch,
                )

                command = ["git", "rm", "-r", folder]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(10):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode if proc.returncode is not None else -999,
                        command,
                        stdout,
                        stderr,
                    )
                commit_args = (
                    ["--amend", "--no-edit"]
                    if git.get("amend", configuration.AMEND_DEFAULT)
                    else ["-m", f"Delete {folder} to clean {context.module_event_data.type} {name}"]
                )
                command = ["git", "commit", *commit_args]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(10):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode if proc.returncode is not None else -999,
                        command,
                        stdout,
                        stderr,
                    )
                command = [
                    "git",
                    "push",
                    *(["--force"] if git.get("amend", configuration.AMEND_DEFAULT) else []),
                    "origin",
                    branch,
                ]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(
                        proc.returncode if proc.returncode is not None else -999,
                        command,
                        stdout,
                        stderr,
                    )
