"""Manage configuration of the application."""

import os
from typing import Any, cast

import github
import jsonmerge
import yaml

from github_app_geo_project import application_configuration, project_configuration

with open(os.environ["GHCI_CONFIGURATION"], encoding="utf-8") as configuration_file:
    APPLICATION_CONFIGURATION: application_configuration.GithubApplicationProjectConfiguration = yaml.load(
        configuration_file, Loader=yaml.SafeLoader
    )


def apply_profile_inheritance(
    profile_name: str, profiles: dict[str, application_configuration.ProfileApplicationSpecificConfiguration]
) -> None:
    """
    Apply the inheritance of the profile.
    """
    for other_name, other_profile in APPLICATION_CONFIGURATION["profiles"].items():
        if profile.get("inherits") == profile_name:
            APPLICATION_CONFIGURATION["profiles"][other_name] = jsonmerge.merge(
                profiles[profile_name], other_profile
            )
            apply_profile_inheritance(name, profiles)


for name, profile in APPLICATION_CONFIGURATION.get("profiles", {}).items():
    if "inherits" not in profile:
        apply_profile_inheritance(
            name,
            cast(
                dict[str, application_configuration.ProfileApplicationSpecificConfiguration],
                APPLICATION_CONFIGURATION["profiles"],
            ),
        )


class GithubObjects:
    """The Github authentication objects."""

    auth: github.Auth.AppAuth
    integration: github.GithubIntegration
    application: github.Github


GITHUB_APPLICATIONS: dict[str, GithubObjects] = {}


def get_github_objects(config: dict[str, Any], application_name: str) -> GithubObjects:
    """Get the Github Application objects by name."""
    applications = config.get("applications", "").split()
    if application_name not in applications:
        raise ValueError(
            f"Application {application_name} not found, available applications: {', '.join(applications)}"
        )
    if application_name not in GITHUB_APPLICATIONS:  # pylint: disable=undefined-variable # noqa
        private_key = "\n".join(
            [
                e.strip()
                for e in config[f"application.{application_name}.github_app_private_key"].strip().split("\n")
            ]
        )
        objects = GithubObjects()
        objects.auth = github.Auth.AppAuth(
            config[f"application.{application_name}.github_app_id"],
            private_key,
        )
        objects.integration = github.GithubIntegration(auth=objects.auth)

        GITHUB_APPLICATIONS[application_name] = objects  # noqa

    objects = GITHUB_APPLICATIONS[application_name]  # noqa

    return objects


def get_github_application(
    config: dict[str, Any], application_name: str, owner: str, repository: str
) -> github.Github:
    """Get the Github Application by name."""
    objects = get_github_objects(config, application_name)

    token = objects.integration.get_access_token(
        objects.integration.get_installation(owner, repository).id
    ).token
    github_application = github.Github(login_or_token=token)

    return github_application


def get_configuration(
    config: dict[str, Any], owner: str, repository: str, application: str
) -> project_configuration.GithubApplicationProjectConfiguration:
    """
    Get the Configuration for the repository.

    Parameter:
        repository: The repository name (<owner>/<name>)
    """
    github_application = get_github_application(config, application, owner, repository)
    repo = github_application.get_repo(f"{owner}/{repository}")
    try:
        project_configuration_content = repo.get_contents(".github/ghci.yaml")
        assert not isinstance(project_configuration_content, list)
        project_custom_configuration = yaml.load(
            project_configuration_content.decoded_content, Loader=yaml.SafeLoader
        )
    except github.GithubException:
        project_custom_configuration = {}

    return jsonmerge.merge(  # type: ignore[no-any-return]
        APPLICATION_CONFIGURATION.get("profiles", {}).get(
            project_custom_configuration.get("profile", APPLICATION_CONFIGURATION.get("default-profile")), {}
        ),
        project_custom_configuration,
    )
