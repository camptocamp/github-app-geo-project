"""Manage configuration of the application."""

import os
from datetime import datetime, timedelta
from typing import cast

import github
import jsonmerge
import yaml

from github_app_geo_project import application_configuration, project_configuration

with open(os.environ["GITHUB_APP_GEO_PROJECT_CONFIGURATION"], encoding="utf-8") as configuration_file:
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
        if profile["inherits"] == profile_name:
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
    token: github.Auth.Token
    token_date: datetime
    application: github.Github


GITHUB_APPLICATIONS: dict[str, GithubObjects]


def get_github_application(application_name: str) -> GithubObjects:
    """Get the Github Application by name."""
    # TODO get from ini config file
    if application_name not in APPLICATION_CONFIGURATION["applications"]:
        raise ValueError(
            f"Application {application_name} not found, available applications: {', '.join(APPLICATION_CONFIGURATION['applications'].keys())}"
        )
    if application_name not in GITHUB_APPLICATIONS:  # pylint: disable=undefined-variable # noqa
        app_config = APPLICATION_CONFIGURATION["applications"][application_name]

        objects = GithubObjects()
        objects.auth = github.AppAuth(app_config["id"], app_config["private-key"])  # type: ignore[attr-defined]
        objects.integration = github.GithubIntegration(auth=objects.auth)

        GITHUB_APPLICATIONS[application_name] = objects  # noqa

    objects = GITHUB_APPLICATIONS[application_name]  # noqa
    if objects.token_date is None or objects.token_date < datetime.now() - timedelta(minutes=30):
        objects.token = objects.integration.get_access_token()  # type: ignore[call-arg,assignment]
        objects.token_date = datetime.now()
        objects.application = github.Github(login_or_token=objects.token.token)

    return objects


def get_configuration(repository: str) -> project_configuration.GithubApplicationProjectConfiguration:
    """
    Get the Configuration for the repository.

    Parameter:
        repository: The repository name (<owner>/<name>)
    """
    github_application = get_github_application(APPLICATION_CONFIGURATION["default-application"])
    repo = github_application.application.get_repo(repository)
    project_configuration_content = repo.get_contents(".github/geo-configuration.yaml")
    assert not isinstance(project_configuration_content, list)
    project_custom_configuration = yaml.load(
        project_configuration_content.decoded_content, Loader=yaml.SafeLoader
    )

    return jsonmerge.merge(  # type: ignore[no-any-return]
        APPLICATION_CONFIGURATION.get("profiles", {}).get(
            project_custom_configuration.get("profile", APPLICATION_CONFIGURATION.get("default-profile")), {}
        ),
        project_custom_configuration,
    )
