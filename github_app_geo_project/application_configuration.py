"""
Automatically generated file from a JSON schema.
"""


from typing import Literal, TypedDict, Union

# Application.
#
# The application configuration
Application = TypedDict(
    "Application",
    {
        # Application ID.
        #
        # The application ID
        "id": str,
        # Application private key.
        #
        # The private key used to authenticate the application
        "private-key": str,
    },
    total=False,
)


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

    additionalProperties: False

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
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof0Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof1Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof2Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof3Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof4Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof5Object", None, bool, None],
    Union[str, Union[int, float], "_ChangelogSectionRoutingConditionAnyof6Object", None, bool, None],
]
"""
Changelog section routing condition.

The condition to match with the changelog items

additionalProperties: False

Aggregation type: anyOf
"""


# GitHub application project configuration.
GithubApplicationProjectConfiguration = TypedDict(
    "GithubApplicationProjectConfiguration",
    {
        # Default profile.
        #
        # The profile name used by default
        "default-profile": str,
        # Default application.
        #
        # The default Github Application to be used
        "default-application": str,
        # Applications.
        #
        # The applications configuration
        "applications": dict[str, "Application"],
        # Profiles.
        #
        # The profiles configuration
        "profiles": dict[str, "_ProfilesAdditionalproperties"],
    },
    total=False,
)


class _ChangelogSectionRoutingConditionAnyof0Object(TypedDict, total=False):
    type: Literal["const"]
    """ The type of the condition """

    value: bool
    """ The value of the condition """


class _ChangelogSectionRoutingConditionAnyof1Object(TypedDict, total=False):
    type: "_ChangelogSectionRoutingConditionAnyof1ObjectType"
    """ The type of the condition """

    conditions: list["ChangelogSectionRoutingCondition"]
    """ The value of the conditions """


_ChangelogSectionRoutingConditionAnyof1ObjectType = Union[Literal["and"], Literal["or"]]
""" The type of the condition """
_CHANGELOGSECTIONROUTINGCONDITIONANYOF1OBJECTTYPE_AND: Literal["and"] = "and"
"""The values for the 'The type of the condition' enum"""
_CHANGELOGSECTIONROUTINGCONDITIONANYOF1OBJECTTYPE_OR: Literal["or"] = "or"
"""The values for the 'The type of the condition' enum"""


class _ChangelogSectionRoutingConditionAnyof2Object(TypedDict, total=False):
    type: Literal["label"]
    """ The type of the condition """

    value: str
    """ The value of the label """


class _ChangelogSectionRoutingConditionAnyof3Object(TypedDict, total=False):
    type: Literal["files"]
    """ The type of the condition """

    regex: list["_ChangelogSectionRoutingConditionAnyof3ObjectRegexItem"]
    """ The list of regex that all the files should match """


_ChangelogSectionRoutingConditionAnyof3ObjectRegexItem = str
""" The regex that all the files should match """


class _ChangelogSectionRoutingConditionAnyof4Object(TypedDict, total=False):
    type: Literal["author"]
    """ The type of the condition """

    value: str
    """ The value of the author """


class _ChangelogSectionRoutingConditionAnyof5Object(TypedDict, total=False):
    type: Literal["title"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


class _ChangelogSectionRoutingConditionAnyof6Object(TypedDict, total=False):
    type: Literal["branch"]
    """ The type of the condition """

    regex: str
    """ The regex the the title should match """


_ProfilesAdditionalproperties = Union["Changelog"]
""" Aggregation type: oneOf """
