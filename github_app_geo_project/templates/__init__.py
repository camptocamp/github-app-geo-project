"""The mako templates to render the pages."""

import logging
from datetime import datetime, timezone

import html_sanitizer
import markdown as markdown_lib  # mypy: ignore[import-untyped]
import markupsafe

_LOGGER = logging.getLogger(__name__)


def sanitizer(text: markupsafe.Markup) -> str:
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


def pprint_date(date_str: markupsafe.Markup) -> str:
    """
    Pretty print the date.
    """
    date = datetime.fromisoformat(date_str)
    full_date = datetime.strftime(date, "%Y-%m-%d %H:%M:%S")

    delta = datetime.now(timezone.utc) - date
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
