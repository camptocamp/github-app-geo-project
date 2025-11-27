"""
Automatically generated file from a JSON schema.
"""


from typing import Literal, TypedDict


AMEND_DEFAULT = False
r""" Default value of the field path 'git amend' """



BRANCH_DEFAULT = 'gh-pages'
r""" Default value of the field path 'git branch' """



class CleanConfiguration(TypedDict, total=False):
    r""" Clean configuration. """

    docker: bool
    r"""
    docker.

    Clean the docker images made from feature branches and pull requests

    default: True
    """

    git: list["Git"]


class CleanModulesConfiguration(TypedDict, total=False):
    r""" Clean modules configuration. """

    clean: "CleanConfiguration"
    r""" Clean configuration. """



DOCKER_DEFAULT = True
r""" Default value of the field path 'Clean configuration docker' """



FOLDER_DEFAULT = '{name}'
r""" Default value of the field path 'git folder' """



# | git.
# | 
# | Clean a folder from a branch
Git = TypedDict('Git', {
    # | on-type.
    # | 
    # | feature_branch, pull_request or all
    # | 
    # | default: all
    'on-type': "OnType",
    # | branch.
    # | 
    # | The branch on witch one the folder will be cleaned
    # | 
    # | default: gh-pages
    'branch': str,
    # | folder.
    # | 
    # | The folder to be cleaned, can contains {name}, that will be replaced with the branch name or pull request number
    # | 
    # | default: {name}
    'folder': str,
    # | amend.
    # | 
    # | If true, the commit will be amended instead of creating a new one
    # | 
    # | default: False
    'amend': bool,
}, total=False)


ON_TYPE_DEFAULT = 'all'
r""" Default value of the field path 'git on-type' """



OnType = Literal['feature_branch'] | Literal['pull_request'] | Literal['all']
r"""
on-type.

feature_branch, pull_request or all

default: all
"""
ONTYPE_FEATURE_BRANCH: Literal['feature_branch'] = "feature_branch"
r"""The values for the 'on-type' enum"""
ONTYPE_PULL_REQUEST: Literal['pull_request'] = "pull_request"
r"""The values for the 'on-type' enum"""
ONTYPE_ALL: Literal['all'] = "all"
r"""The values for the 'on-type' enum"""

