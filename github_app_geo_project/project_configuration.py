"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict, Union

GithubApplicationProjectConfiguration = Union[
    dict[str, "ModuleConfiguration"], "GithubApplicationProjectConfigurationTyped"
]
"""
GitHub application project configuration.


WARNING: Normally the types should be a mix of each other instead of Union.
See: https://github.com/camptocamp/jsonschema-gentypes/issues/7
"""


class GithubApplicationProjectConfigurationTyped(TypedDict, total=False):
    profile: str
    """
    Profile.

    The profile to use for the project
    """


class ModuleConfiguration(TypedDict, total=False):
    """Module configuration."""

    enabled: bool
    """
    Enable the module

    default: True
    """


_MODULE_CONFIGURATION_ENABLED_DEFAULT = True
""" Default value of the field path 'Module configuration enabled' """
