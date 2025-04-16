"""Manage configuration of the application."""

import logging
import os
from pathlib import Path
from typing import Any, NamedTuple, cast

import github
import github as github_lib
import githubkit.versions.latest.models
import githubkit.versions.latest.types
import jsonmerge
import yaml
from deprecated import deprecated

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

    deprecated_integration: github.GithubIntegration
    """The Github integration (Deprecated)"""

    @property
    @deprecated(
        "This property is deprecated and will be removed in a future release, use aio_github instead.",
    )
    def integration(self) -> github.GithubIntegration:
        """The Github integration (Deprecated)."""
        return self.deprecated_integration

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
    deprecated_repo: github_lib.Repository.Repository
    """The repository object (Deprecated)"""

    @property
    @deprecated("This property is deprecated and will be removed in a future release, use aio_repo instead.")
    def repo(self) -> github_lib.Repository.Repository:
        """The repository object (Deprecated)."""
        return self.deprecated_repo

    aio_installation: githubkit.versions.latest.models.Installation
    """The installation object for the repository"""
    aio_github: githubkit.GitHub[githubkit.AppInstallationAuthStrategy]
    """The githubkit object for the repository"""
    aio_repo: githubkit.versions.latest.models.FullRepository
    """The githubkit repository object"""


GITHUB_APPLICATIONS: dict[str, GithubApplication] = {}


async def get_github_application(config: dict[str, Any], application_name: str) -> GithubApplication:
    """Get the Github Application objects by name."""
    applications = config.get("applications", "").split()
    if application_name not in applications:
        message = (
            f"Application {application_name} not found, available applications: {', '.join(applications)}"
        )
        raise ValueError(message)
    if application_name not in GITHUB_APPLICATIONS:  # pylint: disable=undefined-variable
        private_key = "\n".join(
            [
                e.strip()
                for e in config[f"application.{application_name}.github_app_private_key"].strip().split("\n")
            ],
        )
        application_id = config[f"application.{application_name}.github_app_id"]
        auth = github.Auth.AppAuth(application_id, private_key)

        aio_auth = githubkit.AppAuthStrategy(application_id, private_key)
        aio_github = githubkit.GitHub(aio_auth)
        aio_application_response = await aio_github.rest.apps.async_get_authenticated()
        aio_application = aio_application_response.parsed_data
        assert aio_application is not None
        slug = aio_application.slug
        assert isinstance(slug, str)

        objects = GithubApplication(
            github.GithubIntegration(auth=auth, retry=3),
            application_name,
            application_id,
            private_key,
            slug,
            aio_auth,
            aio_github,
            aio_application,
        )

        GITHUB_APPLICATIONS[application_name] = objects

    return GITHUB_APPLICATIONS[application_name]


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

    token = github_application.integration.get_access_token(
        github_application.integration.get_installation(owner, repository).id,
    )
    _LOGGER.debug("Generate token for %s/%s that expire at: %s", owner, repository, token.expires_at)
    github_obj = github.Github(login_or_token=token.token)
    repo = github_obj.get_repo(f"{owner}/{repository}")

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
    aio_repo = (await aio_github.rest.repos.async_get(owner, repository)).parsed_data
    aio_app_auth = aio_github.auth.get_auth_flow(aio_github)
    assert isinstance(aio_app_auth, githubkit.auth.app.AppAuth)

    return GithubProject(
        github_application,
        await aio_app_auth.aget_jwt(),
        owner,
        repository,
        repo,
        aio_installation,
        aio_github,
        aio_repo,
    )


async def get_configuration(
    github_project: GithubProject,
) -> project_configuration.GithubApplicationProjectConfiguration:
    """
    Get the Configuration for the repository.

    Parameter:
        repository: The repository name (<owner>/<name>)
    """
    repo = github_project.repo
    project_custom_configuration = {}
    try:
        project_configuration_content = repo.get_contents(".github/ghci.yaml")
        assert not isinstance(project_configuration_content, list)
        project_custom_configuration = yaml.load(
            project_configuration_content.decoded_content,
            Loader=yaml.SafeLoader,
        )
    except github.GithubException as exception:
        if exception.status != 404:
            raise

    return jsonmerge.merge(  # type: ignore[no-any-return]
        APPLICATION_CONFIGURATION.get("profiles", {}).get(
            project_custom_configuration.get("profile", APPLICATION_CONFIGURATION.get("default-profile")),
            {},
        ),
        project_custom_configuration,
    )
