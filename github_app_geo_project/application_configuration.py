"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict

# Application.
#
# The application configuration
Application = TypedDict(
    "Application",
    {
        # Application ID.
        #
        # The application ID
        "if": str,
        # Application private key.
        #
        # The private key used to authenticate the application
        "private-key": str,
    },
    total=False,
)


class Changelog(TypedDict, total=False):
    """
    Changelog.

    The changelog generation configuration
    """

    enabled: bool
    """
    Enabled.

    Enable the changelog generation

    default: True
    """


ENABLED_DEFAULT = True
""" Default value of the field path 'Changelog enabled' """


# GitHub application project configuration.
GithubApplicationProjectConfiguration = TypedDict(
    "GithubApplicationProjectConfiguration",
    {
        # Default profile.
        #
        # The profile name used by default
        "default-profile": str,
        # Applications.
        #
        # The applications configuration
        "applications": dict[str, "Application"],
        # Profiles.
        #
        # The profiles configuration
        "profiley": dict[str, "Project"],
    },
    total=False,
)


class Project(TypedDict, total=False):
    """
    Project.

    The project configuration
    """

    profile: str
    """
    Profile.

    The profile to use for the project
    """

    changelog: "Changelog"
    """
    Changelog.

    The changelog generation configuration
    """
