"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict


class AutoPullRequest(TypedDict, total=False):
    r"""
    Auto pull request.

    auto pull request configuration
    """

    conditions: list["_ConditionsItem"]
    r""" Conditions. """



# | Auto pull request modules configuration base.
AutoPullRequestModulesConfigurationBase = TypedDict('AutoPullRequestModulesConfigurationBase', {
    # | Auto pull request.
    # | 
    # | auto pull request configuration
    'auto-review': "AutoPullRequest",
    # | Auto pull request.
    # | 
    # | auto pull request configuration
    'auto-merge': "AutoPullRequest",
    # | Auto pull request.
    # | 
    # | auto pull request configuration
    'auto-close': "AutoPullRequest",
}, total=False)


class _ConditionsItem(TypedDict, total=False):
    author: str
    r"""
    Author regex.

    The author of the pull request
    """

    branch: str
    r"""
    Branch regex.

    Regex to match the branch of the pull request
    """

    title: str
    r"""
    Title regex.

    Regex to match the title of the pull request
    """

