"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict


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


class GithubApplicationProjectConfiguration(TypedDict, total=False):
    """GitHub application project configuration."""

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
