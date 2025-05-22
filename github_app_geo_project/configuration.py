"""Manage configuration of the application."""

import base64
import logging
import os
from pathlib import Path
from typing import Any, NamedTuple, cast

import githubkit.cache
import githubkit.exception
import githubkit.versions.latest.models
import jsonmerge
import redis.asyncio.client
import yaml

from github_app_geo_project import application_configuration, project_configuration

_LOGGER = logging.getLogger(__name__)

APPLICATION_CONFIGURATION: application_configuration.GithubApplicationProjectConfiguration = {}
if "GHCI_CONFIGURATION" in os.environ:
    with Path(os.environ["GHCI_CONFIGURATION"]).open(encoding="utf-8") as configuration_file:
        APPLICATION_CONFIGURATION = yaml.load(configuration_file, Loader=yaml.SafeLoader)


def apply_profile_inheritance(profile_name: str, profiles: dict[str, Any]) -> None:
    """Apply the inheritance of the profile."""
    for other_name, other_profile in APPLICATION_CONFIGURATION["profiles"].items():
        if other_profile.get("inherits") == profile_name:
            _LOGGER.debug("Apply inheritance %s -> %s", profile_name, other_name)
            APPLICATION_CONFIGURATION["profiles"][other_name] = jsonmerge.merge(
                profiles[profile_name],
                other_profile,
            )
            del APPLICATION_CONFIGURATION["profiles"][other_name]["inherits"]
            apply_profile_inheritance(name, profiles)


while [True for p in APPLICATION_CONFIGURATION.get("profiles", {}).values() if "inherits" in p]:
    for name, profile in APPLICATION_CONFIGURATION.get("profiles", {}).items():
        if "inherits" not in profile:
            apply_profile_inheritance(
                name,
                cast(
                    "dict[str, Any]",
                    APPLICATION_CONFIGURATION["profiles"],
                ),
            )

_LOGGER.debug("Configuration loaded: %s", APPLICATION_CONFIGURATION)


class GithubApplication(NamedTuple):
    """The Github authentication objects."""

    name: str
    """The application name"""
    id: int
    """The application id"""
    private_key: str
    """The application private key"""
    slug: str
    """The application slug"""
    aio_auth: githubkit.AppAuthStrategy
    """The authentication strategy for the application"""
    aio_github: githubkit.GitHub[githubkit.AppAuthStrategy]
    """The githubkit GitHub"""
    aio_application: githubkit.versions.latest.models.Integration
    """The githubkit application object"""
    aio_cache_strategy: githubkit.cache.BaseCacheStrategy | None
    """The githubkit cache strategy for the application"""


_DEFAULT_BRANCH_CACHE: dict[str, str] = {}


class GithubProject(NamedTuple):
    """The Github Application objects."""

    application: GithubApplication
    """The Github application"""
    token: str
    """The token for the repository"""
    owner: str
    """The owner of the repository"""
    repository: str
    """The repository name"""
    aio_installation: githubkit.versions.latest.models.Installation
    """The installation object for the repository"""
    aio_github: githubkit.GitHub[githubkit.AppInstallationAuthStrategy]
    """The githubkit object for the repository"""

    async def default_branch(self) -> str:
        """Get the default branch of the repository."""
        full_repo = f"{self.owner}/{self.repository}"
        if full_repo in _DEFAULT_BRANCH_CACHE:
            return _DEFAULT_BRANCH_CACHE[full_repo]
        aio_repo = (
            await self.aio_github.rest.repos.async_get(
                owner=self.owner,
                repo=self.repository,
            )
        ).parsed_data
        default_branch = aio_repo.default_branch
        if default_branch is None:
            message = (
                f"Default branch not found for {self.owner}/{self.repository}, "
                f"check if the repository is empty or if the default branch is set"
            )
            raise ValueError(message)
        _DEFAULT_BRANCH_CACHE[full_repo] = default_branch
        return default_branch


async def get_github_application(config: dict[str, Any], application_name: str) -> GithubApplication:
    """Get the Github Application objects by name."""
    applications = config.get("applications", "").split()
    if application_name not in applications:
        message = (
            f"Application {application_name} not found, available applications: {', '.join(applications)}"
        )
        raise ValueError(message)
    private_key = "\n".join(
        [
            e.strip()
            for e in config[f"application.{application_name}.github_app_private_key"].strip().split("\n")
        ],
    )
    application_id = config[f"application.{application_name}.github_app_id"]

    aio_auth = githubkit.AppAuthStrategy(application_id, private_key)
    aio_cache_strategy = (
        githubkit.cache.AsyncRedisCacheStrategy(
            redis.asyncio.client.Redis(
                host=os.environ["REDIS_HOST"],
                port=int(os.environ.get("REDIS_PORT", "6379")),
                db=int(os.environ.get("REDIS_DB", "0")),
                username=os.environ.get("REDIS_USERNAME"),
                password=os.environ.get("REDIS_PASSWORD"),
                ssl=os.environ.get("REDIS_SSL", "false").lower() in ("true", "1", "yes"),
            ),
            prefix="githubkit-",
        )
        if "REDIS_HOST" in os.environ
        else None
    )
    aio_github = githubkit.GitHub(aio_auth, cache_strategy=aio_cache_strategy)
    aio_application_response = await aio_github.rest.apps.async_get_authenticated()
    aio_application = aio_application_response.parsed_data
    assert aio_application is not None
    slug = aio_application.slug
    assert isinstance(slug, str)

    return GithubApplication(
        application_name,
        application_id,
        private_key,
        slug,
        aio_auth,
        aio_github,
        aio_application,
        aio_cache_strategy,
    )


async def get_github_project(
    config: dict[str, Any],
    github_application: GithubApplication | str,
    owner: str,
    repository: str,
) -> GithubProject:
    """Get the Github Application by name."""
    github_application = (
        await get_github_application(config, github_application)
        if isinstance(github_application, str)
        else github_application
    )
    assert isinstance(github_application, GithubApplication)

    aio_installation = (
        await github_application.aio_github.rest.apps.async_get_repo_installation(
            owner,
            repository,
        )
    ).parsed_data
    aoi_installation_auth_strategy = github_application.aio_auth.as_installation(
        aio_installation.id,
    )
    aio_github = github_application.aio_github.with_auth(aoi_installation_auth_strategy)
    aio_app_auth = aio_github.auth.get_auth_flow(aio_github)
    assert isinstance(aio_app_auth, githubkit.auth.app.AppAuth)
    aio_access_token = (
        await aio_github.rest.apps.async_create_installation_access_token(aio_installation.id)
    ).parsed_data
    _LOGGER.debug(
        "Generate token for %s/%s that expire at: %s",
        owner,
        repository,
        aio_access_token.expires_at,
    )

    return GithubProject(
        github_application,
        aio_access_token.token,
        owner,
        repository,
        aio_installation,
        aio_github,
    )


async def get_configuration(
    github_project: GithubProject,
) -> project_configuration.GithubApplicationProjectConfiguration:
    """
    Get the Configuration for the repository.

    Parameter:
        repository: The repository name (<owner>/<name>)
    """
    project_custom_configuration = {}
    try:
        project_configuration_content = (
            await github_project.aio_github.rest.repos.async_get_content(
                owner=github_project.owner,
                repo=github_project.repository,
                path=".github/ghci.yaml",
            )
        ).parsed_data
        assert isinstance(project_configuration_content, githubkit.versions.latest.models.ContentFile)
        assert project_configuration_content is not None
        project_custom_configuration = yaml.load(
            base64.b64decode(project_configuration_content.content).decode("utf-8"),
            Loader=yaml.SafeLoader,
        )
    except githubkit.exception.RequestFailed as exception:
        if exception.response.status_code != 404:
            raise

    return jsonmerge.merge(  # type: ignore[no-any-return]
        APPLICATION_CONFIGURATION.get("profiles", {}).get(
            project_custom_configuration.get("profile", APPLICATION_CONFIGURATION.get("default-profile")),
            {},
        ),
        project_custom_configuration,
    )
