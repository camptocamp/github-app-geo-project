"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict, Union

# GitHub application project configuration.
GithubApplicationProjectConfiguration = TypedDict(
    "GithubApplicationProjectConfiguration",
    {
        # Title.
        #
        # The title of the project
        "title": str,
        # Description.
        #
        # The description of the project
        "description": str,
        # Documentation URL.
        #
        # The URL of the documentation
        "documentation-url": str,
        # Start URL.
        #
        # The URL of the start page
        "start-url": str,
        # Default profile.
        #
        # The profile name used by default
        "default-profile": str,
        # Profiles.
        #
        # The profiles configuration
        "profiles": dict[str, "_ProfilesAdditionalproperties"],
    },
    total=False,
)


class ModuleConfiguration(TypedDict, total=False):
    """Module configuration."""

    enabled: bool
    """ Enable the module """


class ProfileApplicationSpecificConfiguration(TypedDict, total=False):
    """Profile application specific configuration."""

    inherits: str
    """
    Inherits.

    The profile to inherit from
    """


_ProfilesAdditionalproperties = Union[dict[str, "ModuleConfiguration"], "_ProfilesAdditionalpropertiesTyped"]
"""

WARNING: Normally the types should be a mix of each other instead of Union.
See: https://github.com/camptocamp/jsonschema-gentypes/issues/7
Subtype: "ProfileApplicationSpecificConfiguration"
"""


class _ProfilesAdditionalpropertiesTyped(TypedDict, total=False):
    inherits: str
    """
    Inherits.

    The profile to inherit from
    """
