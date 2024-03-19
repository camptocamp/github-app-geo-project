"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict

# Example.
#
# An example of a module properties
Example = TypedDict(
    "Example",
    {
        # Module enabled.
        #
        # Enable the module
        #
        # default: True
        "enabled": bool,
        # Example property.
        #
        # An example property
        "example-property": str,
    },
    total=False,
)


class GithubApplicationProjectConfiguration(TypedDict, total=False):
    """GitHub application project configuration."""

    profile: str
    """
    Profile.

    The profile to use for the project
    """

    example: "Example"
    """
    Example.

    An example of a module properties
    Subtype: "ModuleConfiguration"
    """


MODULE_ENABLED_DEFAULT = True
""" Default value of the field path 'Example enabled' """


class ModuleConfiguration(TypedDict, total=False):
    """Module configuration."""

    enabled: bool
    """
    Module enabled.

    Enable the module

    default: True
    """
