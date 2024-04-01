"""
Automatically generated file from a JSON schema.
"""

from typing import TypedDict


class AutoPullRequest(TypedDict, total=False):
    """
    Auto pull request.

    auto pull request configuration
    """

    conditions: list["_ConditionsItem"]
    """ Conditions. """


# Auto pull request modules configuration base.
AutoPullRequestModulesConfigurationBase = TypedDict(
    "AutoPullRequestModulesConfigurationBase",
    {
        # Auto pull request.
        #
        # auto pull request configuration
        "auto-review": "AutoPullRequest",
        # Auto pull request.
        #
        # auto pull request configuration
        "auto-merge": "AutoPullRequest",
        # Auto pull request.
        #
        # auto pull request configuration
        "auto-close": "AutoPullRequest",
    },
    total=False,
)


class _ConditionsItem(TypedDict, total=False):
    author: str
    """
    Author regex.

    The author of the pull request
    """

    branch: str
    """
    Branch regex.

    Regex to match the branch of the pull request
    """

    title: str
    """
    Title regex.

    Regex to match the title of the pull request
    """
