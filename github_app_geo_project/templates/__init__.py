"""The mako templates to render the pages."""

import html_sanitizer
import markdown as markdown_lib  # mypy: ignore[import-untyped]


def sanitizer(text: str) -> str:
    """
    Sanitize the input string.
    """
    sanitizer_instance = html_sanitizer.Sanitizer()
    return sanitizer_instance.sanitize(text)  # type: ignore[no-any-return]


def markdown(text: str) -> str:
    """
    Convert the input string to markdown.
    """
    return sanitizer(markdown_lib.markdown(text))
