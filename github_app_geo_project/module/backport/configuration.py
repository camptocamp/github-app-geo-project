"""Automatically generated file from a JSON schema."""


from typing import TypedDict


AUTO_CREATE_DEFAULT = True
""" Default value of the field path 'labels auto-create' """



AUTO_DELETE_DEFAULT = True
""" Default value of the field path 'labels auto-delete' """



class BackportConfiguration(TypedDict, total=False):
    """ Backport configuration. """

    labels: "Labels"
    """
    labels.

    The labels configuration
    """



COLOR_DEFAULT = '#5aed94'
""" Default value of the field path 'labels color' """



class CleanModulesConfiguration(TypedDict, total=False):
    """ Clean modules configuration. """

    backport: "BackportConfiguration"
    """ Backport configuration. """



# | labels.
# | 
# | The labels configuration
Labels = TypedDict('Labels', {
    # | auto-create.
    # | 
    # | Create the label if it does not exist
    # | 
    # | default: True
    'auto-create': bool,
    # | auto-delete.
    # | 
    # | Delete the label if it does not exist
    # | 
    # | default: True
    'auto-delete': bool,
    # | color.
    # | 
    # | The color of the label
    # | 
    # | default: #5aed94
    'color': str,
}, total=False)
