"""
Automatically generated file from a JSON schema.
"""


from typing import Literal, TypedDict, Union

# Changelog.
#
# The changelog generation configuration
Changelog = TypedDict(
    "Changelog",
    {
        # Changelog create label.
        #
        # Automatically create the labels used in the changelog configuration
        "create-label": bool,
        # Changelog labels configuration.
        #
        # The labels configuration
        "labels": dict[str, "ChangelogLabelConfiguration"],
        # Changelog sections configuration.
        #
        # The sections configuration
        "sections": list["Section"],
        # Changelog default section.
        #
        # The default section for items
        "default-section": str,
        # Routing.
        #
        # The routing configuration
        "routing": list["ChangelogRoutingConfiguration"],
    },
    total=False,
)


class ChangelogLabelConfiguration(TypedDict, total=False):
    """
    Changelog label configuration.

    The label configuration
    """

    description: str
    """
    Changelog label description.

    The description of the label
    """

    color: str
    """
    Changelog label color.

    The color of the label
    """


class ChangelogRoutingConfiguration(TypedDict, total=False):
    """
    Changelog routing configuration.

    The routing configuration
    """

    section: str
    """ The section section affected to changelog items that match with the conditions """

    condition: "Condition"
    """
    Condition.

    The condition to match with the changelog items

    Aggregation type: anyOf
    Subtype: "ConditionConst", "ConditionAndSolidusOr", "ConditionNot", "ConditionLabel", "ConditionFiles", "ConditionAuthor", "ConditionTitle", "ConditionBranch"
    """


Condition = Union[
    "ConditionConst",
    "ConditionAndSolidusOr",
    "ConditionNot",
    "ConditionLabel",
    "ConditionFiles",
    "ConditionAuthor",
    "ConditionTitle",
    "ConditionBranch",
]
"""
Condition.

The condition to match with the changelog items

Aggregation type: anyOf
Subtype: "ConditionConst", "ConditionAndSolidusOr", "ConditionNot", "ConditionLabel", "ConditionFiles", "ConditionAuthor", "ConditionTitle", "ConditionBranch"
"""


class ConditionAndSolidusOr(TypedDict, total=False):
    """Condition and/or."""

    type: "_ConditionAndSolidusOrType"
    """ The type of the condition """

    conditions: list["ConditionAndSolidusOr"]
    """ The value of the conditions """


class ConditionAuthor(TypedDict, total=False):
    """Condition author."""

    type: Literal["author"]
    """ The type of the condition """

    value: str
    """ The value of the author """


class ConditionBranch(TypedDict, total=False):
    """Condition branch."""

    type: Literal["branch"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


class ConditionConst(TypedDict, total=False):
    """Condition const."""

    type: Literal["const"]
    """ The type of the condition """

    value: bool
    """ The value of the condition """


class ConditionFiles(TypedDict, total=False):
    """Condition files."""

    type: Literal["files"]
    """ The type of the condition """

    regex: list["_ConditionFilesRegexItem"]
    """ The list of regex that all the files should match """


class ConditionLabel(TypedDict, total=False):
    """Condition label."""

    type: Literal["label"]
    """ The type of the condition """

    value: str
    """ The value of the label """


class ConditionNot(TypedDict, total=False):
    """Condition not."""

    type: "_ConditionNotType"
    """ The type of the condition """

    condition: "ConditionNot"
    """ Condition not. """


class ConditionTitle(TypedDict, total=False):
    """Condition title."""

    type: Literal["title"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


class GithubApplicationProjectConfiguration(TypedDict, total=False):
    """GitHub application project configuration."""

    changelog: "Changelog"
    """
    Changelog.

    The changelog generation configuration
    """


class Section(TypedDict, total=False):
    """
    section.

    The section configuration
    """

    name: str
    """ The name of the section """

    title: str
    """ The title of the section """

    description: str
    """ The description of the section """


_ConditionAndSolidusOrType = Union[Literal["and"], Literal["or"]]
""" The type of the condition """
_CONDITIONANDSOLIDUSORTYPE_AND: Literal["and"] = "and"
"""The values for the 'The type of the condition' enum"""
_CONDITIONANDSOLIDUSORTYPE_OR: Literal["or"] = "or"
"""The values for the 'The type of the condition' enum"""


_ConditionFilesRegexItem = str
""" The regex that all the files should match """


_ConditionNotType = Union[Literal["not"]]
""" The type of the condition """
_CONDITIONNOTTYPE_NOT: Literal["not"] = "not"
"""The values for the 'The type of the condition' enum"""