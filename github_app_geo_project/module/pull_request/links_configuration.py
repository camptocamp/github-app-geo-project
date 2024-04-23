"""
Automatically generated file from a JSON schema.
"""

from typing import TypedDict

BRANCH_PATTERNS_DEFAULT = [
    "^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$",
    "^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$",
    "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$",
    "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$",
]
""" Default value of the field path 'Pull request add links configuration branch-patterns' """


# Pull request add links configuration.
PullRequestAddLinksConfiguration = TypedDict(
    "PullRequestAddLinksConfiguration",
    {
        # Branch patterns.
        #
        # List of regular expressions used to get parameters form the branch names
        #
        # default:
        #   - ^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$
        #   - ^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$
        #   - ^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$
        #   - ^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$
        "branch-patterns": list[str],
        # Blacklist.
        #
        # List of regular expressions used to exclude some parameters values
        "blacklist": dict[str, list[str]],
        # Uppercase.
        #
        # List of parameters to convert to uppercase
        "uppercase": list[str],
        # Lowercase.
        #
        # List of parameters to convert to lowercase
        "lowercase": list[str],
        # Content.
        #
        # List of elements to add to the pull request
        "content": list["_ContentItem"],
    },
    total=False,
)


_CONTENT_ITEM_TEXT_DEFAULT = ""
""" Default value of the field path 'Content item text' """


class _ContentItem(TypedDict, total=False):
    text: str
    """ default:  """

    url: str
    requires: list[str]
