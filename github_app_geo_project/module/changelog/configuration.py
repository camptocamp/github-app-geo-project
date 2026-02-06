"""
Automatically generated file from a JSON schema.
"""


from typing import Literal, TypedDict, Union


CREATE_LABELS_DEFAULT = False
r""" Default value of the field path 'Changelog create-labels' """



CREATE_RELEASE_DEFAULT = True
r""" Default value of the field path 'Changelog create-release' """



# | Changelog.
# | 
# | The changelog generation configuration
Changelog = TypedDict('Changelog', {
    # | Create labels.
    # | 
    # | Automatically create the labels used in the changelog configuration
    # | 
    # | default: False
    'create-labels': bool,
    # | Create release.
    # | 
    # | Create a release based on the tag
    # | 
    # | default: True
    'create-release': bool,
    # | Changelog labels configuration.
    # | 
    # | The labels configuration
    'labels': dict[str, "ChangelogLabelConfiguration"],
    # | Changelog sections configuration.
    # | 
    # | The sections configuration
    'sections': list["Section"],
    # | Changelog default section.
    # | 
    # | The default section for items
    'default-section': str,
    # | Routing.
    # | 
    # | The routing configuration
    'routing': list["ChangelogRoutingConfiguration"],
}, total=False)


class ChangelogConfigurationBase(TypedDict, total=False):
    r""" Changelog configuration Base. """

    changelog: "Changelog"
    r"""
    Changelog.

    The changelog generation configuration
    """



class ChangelogLabelConfiguration(TypedDict, total=False):
    r"""
    Changelog label configuration.

    The label configuration
    """

    description: str
    r"""
    Changelog label description.

    The description of the label
    """

    color: str
    r"""
    Changelog label color.

    The color of the label
    """



class ChangelogRoutingConfiguration(TypedDict, total=False):
    r"""
    Changelog routing configuration.

    The routing configuration
    """

    section: str
    r""" The section section affected to changelog items that match with the conditions """

    name: str
    r""" The name of the routing condition """

    condition: Union["ConditionConst", "ConditionAndSolidusOr", "ConditionNot", "ConditionLabel", "ConditionFiles", "ConditionAuthor", "ConditionTitle", "ConditionBranch"]
    r"""
    Condition.

    The condition to match with the changelog items

    Aggregation type: oneOf
    Subtype: "ConditionConst", "ConditionAndSolidusOr", "ConditionNot", "ConditionLabel", "ConditionFiles", "ConditionAuthor", "ConditionTitle", "ConditionBranch"
    """



class ConditionAndSolidusOr(TypedDict, total=False):
    r""" Condition and/or. """

    type: "_ConditionAndSolidusOrType"
    r""" The type of the condition """

    conditions: list["ConditionAndSolidusOr"]
    r""" The value of the conditions """



class ConditionAuthor(TypedDict, total=False):
    r""" Condition author. """

    type: Literal['author']
    r""" The type of the condition """

    value: str
    r""" The value of the author """



class ConditionBranch(TypedDict, total=False):
    r""" Condition branch. """

    type: Literal['branch']
    r""" The type of the condition """

    regex: str
    r""" The regex the branch should match """



class ConditionConst(TypedDict, total=False):
    r""" Condition const. """

    type: Literal['const']
    r""" The type of the condition """

    value: bool
    r""" The value of the condition """



class ConditionFiles(TypedDict, total=False):
    r""" Condition files. """

    type: Literal['files']
    r""" The type of the condition """

    regex: list["_ConditionFilesRegexItem"]
    r""" The list of regex that all the files should match """



class ConditionLabel(TypedDict, total=False):
    r""" Condition label. """

    type: Literal['label']
    r""" The type of the condition """

    value: str
    r""" The value of the label """



class ConditionNot(TypedDict, total=False):
    r""" Condition not. """

    type: "_ConditionNotType"
    r""" The type of the condition """

    condition: "ConditionNot"
    r""" Condition not. """



class ConditionTitle(TypedDict, total=False):
    r""" Condition title. """

    type: Literal['title']
    r""" The type of the condition """

    regex: str
    r""" The regex the title should match """



class Section(TypedDict, total=False):
    r"""
    section.

    The section configuration
    """

    name: str
    r""" The name of the section """

    title: str
    r""" The title of the section """

    description: str
    r""" The description of the section """

    closed: bool
    r"""
    Whether the section content is collapsed by default in the changelog

    default: False
    """



_ConditionAndSolidusOrType = Literal['and'] | Literal['or']
r""" The type of the condition """
_CONDITIONANDSOLIDUSORTYPE_AND: Literal['and'] = "and"
r"""The values for the 'The type of the condition' enum"""
_CONDITIONANDSOLIDUSORTYPE_OR: Literal['or'] = "or"
r"""The values for the 'The type of the condition' enum"""



_ConditionFilesRegexItem = str
r""" The regex that all the files should match """



_ConditionNotType = Literal['not']
r""" The type of the condition """
_CONDITIONNOTTYPE_NOT: Literal['not'] = "not"
r"""The values for the 'The type of the condition' enum"""



_SECTION_CLOSED_DEFAULT = False
r""" Default value of the field path 'section closed' """

