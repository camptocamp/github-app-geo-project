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
    sanitizer_instance = html_sanitizer.Sanitizer(
        {
            "keep_typographic_whitespace": True,
            "tags": html_sanitizer.sanitizer.DEFAULT_SETTINGS["tags"] | {"span", "pre", "code"},
            "attributes": html_sanitizer.sanitizer.DEFAULT_SETTINGS["attributes"]
            | {"span": ("style", "class"), "p": ("style", "class")},
            "separate": html_sanitizer.sanitizer.DEFAULT_SETTINGS["separate"] | {"pre", "code", "span"},
        }
    )
    return sanitizer_instance.sanitize(text)  # type: ignore[no-any-return]


def markdown(text: str) -> str:
    """
    Convert the input string to markdown.
    """
    return sanitizer(markdown_lib.markdown(text))


def pprint_date(date_in: str | datetime) -> str:
    """
    Pretty print a date.
    """
    if date_in == "None" or date_in is None:
        return "-"

    date = datetime.fromisoformat(date_in) if isinstance(date_in, str) else date_in
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


def pprint_duration(duration_in: str | timedelta) -> str:
    """
    Pretty print a duration.
    """
    if duration_in == "None" or duration_in is None:
        return "-"

    if isinstance(duration_in, str):
        if " days, " in duration_in:
            day_, duration_in = duration_in.split(" days, ")
            day = int(day_)
            date = datetime.strptime(duration_in, "%H:%M:%S")
        else:
            day = 0
            date = datetime.strptime(duration_in, "%H:%M:%S.%f")
        duration = timedelta(
            days=day, hours=date.hour, minutes=date.minute, seconds=date.second, microseconds=date.microsecond
        )
    else:
        duration = duration_in

    if duration.total_seconds() < 60:
        return f"{duration.seconds} seconds"
    elif duration.total_seconds() < 3600:
        return f"{duration.seconds // 60} minutes"
    elif duration.total_seconds() < 86400:
        return f"{duration.seconds // 3600} hours"
    else:
        return f"{duration.days} days"
