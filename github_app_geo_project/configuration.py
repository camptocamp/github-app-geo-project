"""Manage configuration of the application."""

import logging
import os
from typing import Any, NamedTuple, cast

import github
import github as github_lib
import jsonmerge
import yaml

from github_app_geo_project import application_configuration, project_configuration

_LOGGER = logging.getLogger(__name__)

APPLICATION_CONFIGURATION: application_configuration.GithubApplicationProjectConfiguration = {}
if "GHCI_CONFIGURATION" in os.environ:
    with open(os.environ["GHCI_CONFIGURATION"], encoding="utf-8") as configuration_file:
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
                    dict[str, Any],
                    APPLICATION_CONFIGURATION["profiles"],
                ),
            )

_LOGGER.debug("Configuration loaded: %s", APPLICATION_CONFIGURATION)


class GithubApplication(NamedTuple):
    """The Github authentication objects."""

    auth: github.Auth.AppAuth
    """Alias of integration.auth"""
    integration: github.GithubIntegration
    name: str
    """The application name"""


class GithubProject(NamedTuple):
    """The Github Application objects."""

    application: GithubApplication
    token: str
    github: github.Github
    owner: str
    """The owner of the repository"""
    repository: str
    """The repository name"""
    repo: github_lib.Repository.Repository


GITHUB_APPLICATIONS: dict[str, GithubApplication] = {}


def get_github_application(config: dict[str, Any], application_name: str) -> GithubApplication:
    """Get the Github Application objects by name."""
    applications = config.get("applications", "").split()
    if application_name not in applications:
        raise ValueError(
            f"Application {application_name} not found, available applications: {', '.join(applications)}",
        )
    if application_name not in GITHUB_APPLICATIONS:  # pylint: disable=undefined-variable
        private_key = "\n".join(
            [
                e.strip()
                for e in config[f"application.{application_name}.github_app_private_key"].strip().split("\n")
            ],
        )
        auth = github.Auth.AppAuth(
            config[f"application.{application_name}.github_app_id"],
            private_key,
        )
        objects = GithubApplication(auth, github.GithubIntegration(auth=auth, retry=3), application_name)

        GITHUB_APPLICATIONS[application_name] = objects

    objects = GITHUB_APPLICATIONS[application_name]

    return objects


def get_github_project(
    config: dict[str, Any],
    application: GithubApplication | str,
    owner: str,
    repository: str,
) -> GithubProject:
    """Get the Github Application by name."""
    objects = get_github_application(config, application) if isinstance(application, str) else application

    token = objects.integration.get_access_token(objects.integration.get_installation(owner, repository).id)
    _LOGGER.debug("Generate token for %s/%s that expire at: %s", owner, repository, token.expires_at)
    github_application = github.Github(login_or_token=token.token)
    repo = github_application.get_repo(f"{owner}/{repository}")

    return GithubProject(objects, token.token, github_application, owner, repository, repo)


def get_configuration(
    config: dict[str, Any],
    owner: str,
    repository: str,
    application: str,
) -> project_configuration.GithubApplicationProjectConfiguration:
    """
    Get the Configuration for the repository.

    Parameter:
        repository: The repository name (<owner>/<name>)
    """
    github_app = get_github_project(config, application, owner, repository)
    repo = github_app.github.get_repo(f"{owner}/{repository}")
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
