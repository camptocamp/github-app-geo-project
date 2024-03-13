"""The mako templates to render the pages."""

from datetime import datetime

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


def pprint_date(date: datetime) -> str:
    """
    Pretty print the date.
    """
    full_date = date.strftime("%Y-%m-%d %H:%M:%S")

    delta = datetime.now() - date
    if delta.seconds < 1:
        short_date = "now"
    elif delta.seconds < 60:
        short_date = f"{delta.seconds} seconds ago"
    elif delta.seconds < 3600:
        short_date = f"{delta.seconds // 60} minutes ago"
    elif delta.seconds < 86400:
        short_date = f"{delta.seconds // 3600} hours ago"
    elif delta.days < 30:
        short_date = f"{delta.days} days ago"
    else:
        short_date = date.strftime("%Y-%m-%d")

    return f'<span title="{full_date}">{short_date}</span>'
