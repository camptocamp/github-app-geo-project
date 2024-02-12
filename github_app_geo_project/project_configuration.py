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
        "sections": list["ChangelogSectionConfiguration"],
        # Changelog default section.
        #
        # The default section for items
        "default-section": str,
        # Changelog routing configuration.
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
    """
    Changelog routing section.

    The section section affected to changelog items that match with the conditions
    """

    condition: "ChangelogSectionRoutingCondition"
    """
    Changelog section routing condition.

    The condition to match with the changelog items

    Aggregation type: anyOf
    """


class ChangelogSectionConfiguration(TypedDict, total=False):
    """
    Changelog section configuration.

    The section configuration
    """

    description: str
    """
    Changelog section description.

    The description of the section
    """


ChangelogSectionRoutingCondition = Union[
    "_ChangelogSectionRoutingConditionAnyof0",
    "_ChangelogSectionRoutingConditionAnyof1",
    "_ChangelogSectionRoutingConditionAnyof2",
    "_ChangelogSectionRoutingConditionAnyof3",
    "_ChangelogSectionRoutingConditionAnyof4",
    "_ChangelogSectionRoutingConditionAnyof5",
    "_ChangelogSectionRoutingConditionAnyof6",
]
"""
Changelog section routing condition.

The condition to match with the changelog items

Aggregation type: anyOf
"""


GithubApplicationProjectConfiguration = Union[
    dict[str, "_GithubApplicationProjectConfigurationAdditionalproperties"],
    "GithubApplicationProjectConfigurationTyped",
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
    """ Enable the module """

    application: str
    """
    Application.

    The GitHub application used by the module
    """


class _ChangelogSectionRoutingConditionAnyof0(TypedDict, total=False):
    type: Literal["const"]
    """ The type of the condition """

    value: bool
    """ The value of the condition """


class _ChangelogSectionRoutingConditionAnyof1(TypedDict, total=False):
    type: "_ChangelogSectionRoutingConditionAnyof1Type"
    """ The type of the condition """

    conditions: list["_ChangelogSectionRoutingConditionAnyof1"]
    """ The value of the conditions """


_ChangelogSectionRoutingConditionAnyof1Type = Union[Literal["and"], Literal["or"]]
""" The type of the condition """
_CHANGELOGSECTIONROUTINGCONDITIONANYOF1TYPE_AND: Literal["and"] = "and"
"""The values for the 'The type of the condition' enum"""
_CHANGELOGSECTIONROUTINGCONDITIONANYOF1TYPE_OR: Literal["or"] = "or"
"""The values for the 'The type of the condition' enum"""


class _ChangelogSectionRoutingConditionAnyof2(TypedDict, total=False):
    type: Literal["label"]
    """ The type of the condition """

    value: str
    """ The value of the label """


class _ChangelogSectionRoutingConditionAnyof3(TypedDict, total=False):
    type: Literal["files"]
    """ The type of the condition """

    regex: list["_ChangelogSectionRoutingConditionAnyof3RegexItem"]
    """ The list of regex that all the files should match """


_ChangelogSectionRoutingConditionAnyof3RegexItem = str
""" The regex that all the files should match """


class _ChangelogSectionRoutingConditionAnyof4(TypedDict, total=False):
    type: Literal["author"]
    """ The type of the condition """

    value: str
    """ The value of the author """


class _ChangelogSectionRoutingConditionAnyof5(TypedDict, total=False):
    type: Literal["title"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


class _ChangelogSectionRoutingConditionAnyof6(TypedDict, total=False):
    type: Literal["branch"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


_GithubApplicationProjectConfigurationAdditionalproperties = TypedDict(
    "_GithubApplicationProjectConfigurationAdditionalproperties",
    {
        # Enable the module
        "enabled": bool,
        # Application.
        #
        # The GitHub application used by the module
        "application": str,
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
        "sections": list["ChangelogSectionConfiguration"],
        # Changelog default section.
        #
        # The default section for items
        "default-section": str,
        # Changelog routing configuration.
        #
        # The routing configuration
        "routing": list["ChangelogRoutingConfiguration"],
    },
    total=False,
)
