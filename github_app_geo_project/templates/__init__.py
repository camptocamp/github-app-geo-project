"""The mako templates to render the pages."""

import html_sanitizer
import markdown


def sanitizer(str) -> str:
    """
    Sanitize the input string.
    """
    sanitizer = html_sanitizer.Sanitizer()
    return sanitizer.sanitize(str)


def markdown(str) -> str:
    """
    Convert the input string to markdown.
    """
    return sanitizer(markdown.markdown(str))
