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


class ModuleConfiguration(TypedDict, total=False):
    """Module configuration."""

    enabled: bool
    """
    Enable the module

    default: True
    """


_EXAMPLE_ENABLED_DEFAULT = True
""" Default value of the field path 'Example enabled' """


_MODULE_CONFIGURATION_ENABLED_DEFAULT = True
""" Default value of the field path 'Module configuration enabled' """
