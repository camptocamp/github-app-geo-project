"""
Automatically generated file from a JSON schema.
"""


from typing import Any, TypedDict

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
        "profiles": dict[str, dict[str, Any]],
    },
    total=False,
)
