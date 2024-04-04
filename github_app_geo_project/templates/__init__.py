"""The mako templates to render the pages."""

import logging
from datetime import datetime, timedelta, timezone

import html_sanitizer
import markdown as markdown_lib  # mypy: ignore[import-untyped]

_LOGGER = logging.getLogger(__name__)


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


def pprint_date(date_str: str) -> str:
    """
    Pretty print a date.
    """
    if date_str == "None":
        return "-"

    date = datetime.fromisoformat(date_str)
    full_date = datetime.strftime(date, "%Y-%m-%d %H:%M:%S")

    delta = datetime.now(timezone.utc) - date
    if delta.total_seconds() < 1:
        short_date = "now"
    elif delta.total_seconds() < 60:
        short_date = f"{delta.seconds} seconds ago"
    elif delta.total_seconds() < 3600:
        short_date = f"{delta.seconds // 60} minutes ago"
    elif delta.total_seconds() < 86400:
        short_date = f"{delta.seconds // 3600} hours ago"
    elif delta.days < 30:
        short_date = f"{delta.days} days ago"
    else:
        short_date = date.strftime("%Y-%m-%d")

    return f'<span title="{full_date}">{short_date}</span>'


def pprint_duration(duration_str: str) -> str:
    """
    Pretty print a duration.
    """
    if duration_str == "None":
        return "-"

    if " days, " in duration_str:
        day_, duration_str = duration_str.split(" days, ")
        day = int(day_)
        date = datetime.strptime(duration_str, "%H:%M:%S")
    else:
        day = 0
        date = datetime.strptime(duration_str, "%H:%M:%S.%f")
    duration = timedelta(
        days=day, hours=date.hour, minutes=date.minute, seconds=date.second, microseconds=date.microsecond
    )

    if duration.total_seconds() < 60:
        return f"{duration.seconds} seconds"
    elif duration.total_seconds() < 3600:
        return f"{duration.seconds // 60} minutes"
    elif duration.total_seconds() < 86400:
        return f"{duration.seconds // 3600} hours"
    else:
        return f"{duration.days} days"
