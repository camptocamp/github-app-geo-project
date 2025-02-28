"""
Automatically generated file from a JSON schema.
"""

from typing import Literal, TypedDict


BRANCH_DEFAULT = "gh-pages"
""" Default value of the field path 'git branch' """


class CleanConfiguration(TypedDict, total=False):
    """Clean configuration."""

    docker: bool
    """
    docker.

    Clean the docker images made from feature branches and pull requests

    default: True
    """

    git: list["Git"]


class CleanModulesConfiguration(TypedDict, total=False):
    """Clean modules configuration."""

    clean: "CleanConfiguration"
    """ Clean configuration. """


DOCKER_DEFAULT = True
""" Default value of the field path 'Clean configuration docker' """


FOLDER_DEFAULT = "{name}"
""" Default value of the field path 'git folder' """


# | git.
# |
# | Clean a folder from a branch
Git = TypedDict(
    "Git",
    {
        # | on-type.
        # |
        # | feature_branch, pull_request or all
        # |
        # | default: all
        "on-type": "OnType",
        # | branch.
        # |
        # | The branch on witch one the folder will be cleaned
        # |
        # | default: gh-pages
        "branch": str,
        # | folder.
        # |
        # | The folder to be cleaned, can contains {name}, that will be replaced with the branch name or pull request number
        # |
        # | default: {name}
        "folder": str,
    },
    total=False,
)


ON_TYPE_DEFAULT = "all"
""" Default value of the field path 'git on-type' """


OnType = Literal["feature_branch"] | Literal["pull_request"] | Literal["all"]
"""
on-type.

feature_branch, pull_request or all

default: all
"""
ONTYPE_FEATURE_BRANCH: Literal["feature_branch"] = "feature_branch"
"""The values for the 'on-type' enum"""
ONTYPE_PULL_REQUEST: Literal["pull_request"] = "pull_request"
"""The values for the 'on-type' enum"""
ONTYPE_ALL: Literal["all"] = "all"
"""The values for the 'on-type' enum"""
