"""
Automatically generated file from a JSON schema.
"""

from typing import TypedDict

EXAMPLE_MODULE_ENABLED_DEFAULT = True
""" Default value of the field path 'Example enabled' """


# | Example.
# |
# | An example of a module properties
Example = TypedDict(
    "Example",
    {
        # | Example module enabled.
        # |
        # | Enable the module
        # |
        # | default: True
        "enabled": bool,
        # | Example property.
        # |
        # | An example property
        "example-property": str,
    },
    total=False,
)


# | GitHub application project configuration.
GithubApplicationProjectConfiguration = TypedDict(
    "GithubApplicationProjectConfiguration",
    {
        # | Profile.
        # |
        # | The profile to use for the project
        "profile": str,
        # | Module configuration.
        "module-configuration": "ModuleConfiguration",
        # | Example.
        # |
        # | An example of a module properties
        "example": "Example",
    },
    total=False,
)


MODULE_ENABLED_DEFAULT = True
""" Default value of the field path 'Module configuration enabled' """


class ModuleConfiguration(TypedDict, total=False):
    """Module configuration."""

    enabled: bool
    """
    Module enabled.

    Enable the module

    default: True
    """
