"""
Automatically generated file from a JSON schema.
"""

from typing import TypedDict, Union

CODESPELL_ARGUMENTS_DEFAULT = ["--quiet-level=2", "--check-filenames", "--ignore-words-list=ro"]
""" Default value of the field path 'Codespell arguments' """


CODESPELL_DICTIONARIES_DEFAULT = ["clear", "rare", "informal", "code", "names", "en-GB_to_en-US"]
""" Default value of the field path 'Codespell internal-dictionaries' """


CODESPELL_IGNORE_REGULAR_EXPRESSION_DEFAULT = ["(.*/)?poetry\\.lock", "(.*/)?package-lock\\.json"]
""" Default value of the field path 'Codespell ignore-re' """


# | Codespell.
# |
# | The codespell check configuration
Codespell = TypedDict(
    "Codespell",
    {
        # | codespell dictionaries.
        # |
        # | List of argument that will be added to the codespell command
        # |
        # | default:
        # |   - clear
        # |   - rare
        # |   - informal
        # |   - code
        # |   - names
        # |   - en-GB_to_en-US
        "internal-dictionaries": list[str],
        # | codespell arguments.
        # |
        # | List of argument that will be added to the codespell command
        # |
        # | default:
        # |   - --quiet-level=2
        # |   - --check-filenames
        # |   - --ignore-words-list=ro
        "arguments": list[str],
        # | codespell ignore regular expression.
        # |
        # | List of regular expression that should be ignored
        # |
        # | default:
        # |   - (.*/)?poetry\.lock
        # |   - (.*/)?package-lock\.json
        "ignore-re": list[str],
    },
    total=False,
)


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIRST_CAPITAL_DEFAULT = True
""" Default value of the field path 'pull request checks commits messages configuration check-first-capital' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_FIXUP_DEFAULT = True
""" Default value of the field path 'pull request checks commits messages configuration check-fixup' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_MIN_HEAD_LENGTH_DEFAULT = 5
""" Default value of the field path 'pull request checks commits messages configuration min-head-length' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_NO_MERGE_COMMITS_DEFAULT = True
""" Default value of the field path 'pull request checks commits messages configuration check-no-merge-commits' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_NO_OWN_REVERT_DEFAULT = True
""" Default value of the field path 'pull request checks commits messages configuration check-no-own-revert' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_ONLY_HEAD_DEFAULT = True
""" Default value of the field path 'pull request checks commits spelling configuration only-head' """


PULL_REQUEST_CHECKS_COMMITS_MESSAGES_SQUASH_DEFAULT = True
""" Default value of the field path 'pull request checks commits messages configuration check-squash' """


PULL_REQUEST_CHECKS_ONLY_HEAD_DEFAULT = True
""" Default value of the field path 'pull request checks pull request spelling configuration only-head' """


# | pull request checks commits messages configuration.
# |
# | The commit message check configuration
PullRequestChecksCommitsMessagesConfiguration = TypedDict(
    "PullRequestChecksCommitsMessagesConfiguration",
    {
        # | pull request checks commits messages fixup.
        # |
        # | Check that we don't have one fixup commit in the pull request
        # |
        # | default: True
        "check-fixup": bool,
        # | pull request checks commits messages squash.
        # |
        # | Check that we don't have one squash commit in the pull request
        # |
        # | default: True
        "check-squash": bool,
        # | pull request checks commits messages first capital.
        # |
        # | Check that the all the commits message starts with a capital letter
        # |
        # | default: True
        "check-first-capital": bool,
        # | pull request checks commits messages min head length.
        # |
        # | Check that the commits message head is at least this long, use 0 to disable
        # |
        # | default: 5
        "min-head-length": int,
        # | pull request checks commits messages no merge commits.
        # |
        # | Check that we don't have merge commits in the pull request
        # |
        # | default: True
        "check-no-merge-commits": bool,
        # | pull request checks commits messages no own revert.
        # |
        # | Check that we don't have reverted one of our commits in the pull request
        # |
        # | default: True
        "check-no-own-revert": bool,
    },
    total=False,
)


# | pull request checks commits spelling configuration.
# |
# | Configuration used to check the spelling of the commits
PullRequestChecksCommitsSpellingConfiguration = TypedDict(
    "PullRequestChecksCommitsSpellingConfiguration",
    {
        # | pull request checks commits messages only head.
        # |
        # | default: True
        "only-head": bool,
    },
    total=False,
)


# | Pull request checks configuration.
PullRequestChecksConfiguration = TypedDict(
    "PullRequestChecksConfiguration",
    {
        # | Codespell.
        # |
        # | The codespell check configuration
        "codespell": "Codespell",
        # | pull request checks commits messages.
        # |
        # | Check the pull request commits messages
        # |
        # | Aggregation type: oneOf
        # | Subtype: "PullRequestChecksCommitsMessagesConfiguration"
        "commits-messages": Union["PullRequestChecksCommitsMessagesConfiguration", bool],
        # | pull request checks commits spelling.
        # |
        # | Aggregation type: oneOf
        # | Subtype: "PullRequestChecksCommitsSpellingConfiguration"
        "commits-spell": Union["PullRequestChecksCommitsSpellingConfiguration", bool],
        # | pull request checks pull request spelling.
        # |
        # | Aggregation type: oneOf
        # | Subtype: "PullRequestChecksPullRequestSpellingConfiguration"
        "pull-request-spell": Union["PullRequestChecksPullRequestSpellingConfiguration", bool],
    },
    total=False,
)


# | pull request checks pull request spelling configuration.
# |
# | Configuration used to check the spelling of the title and body of the pull request
PullRequestChecksPullRequestSpellingConfiguration = TypedDict(
    "PullRequestChecksPullRequestSpellingConfiguration",
    {
        # | pull request checks only head.
        # |
        # | default: True
        "only-head": bool,
    },
    total=False,
)
