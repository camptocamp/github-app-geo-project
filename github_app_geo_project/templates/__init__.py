"""The mako templates to render the pages."""

import datetime
import logging

import html_sanitizer
import markdown as markdown_lib

_LOGGER = logging.getLogger(__name__)


def sanitizer(text: str) -> str:
    """Sanitize the input string."""
    sanitizer_instance = html_sanitizer.Sanitizer(
        {
            "tags": html_sanitizer.sanitizer.DEFAULT_SETTINGS["tags"] | {"span", "div", "pre", "code"},
            "attributes": {
                "a": (
                    "id",
                    "href",
                    "name",
                    "target",
                    "title",
                    "rel",
                    "style",
                    "class",
                    "data-bs-toggle",
                    "role",
                    "aria-expanded",
                    "aria-controls",
                ),
                "span": ("id", "style", "class"),
                "p": ("id", "style", "class"),
                "div": ("id", "style", "class"),
                "em": ("id", "style", "class"),
            },
            "separate": html_sanitizer.sanitizer.DEFAULT_SETTINGS["separate"]
            | {"pre", "code", "span", "div", "em"},
            "empty": {"hr", "br"},
            "keep_typographic_whitespace": True,
            "element_preprocessors": [],
        },
    )
    return sanitizer_instance.sanitize(text)  # type: ignore[no-any-return]


def markdown(text: str) -> str:
    """Convert the input string to markdown."""
    return sanitizer(markdown_lib.markdown(text))


def pprint_short_date(date_in: str | datetime.datetime) -> str:
    """Pretty print a short date (essentially time to current time)."""
    if date_in == "None" or date_in is None:
        return "-"

    date = datetime.datetime.fromisoformat(date_in) if isinstance(date_in, str) else date_in

    delta = datetime.datetime.now(datetime.UTC) - date
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

    return short_date


def pprint_full_date(date_in: str | datetime.datetime) -> str:
    """Pretty print a full date."""
    if date_in == "None" or date_in is None:
        return "-"

    date = datetime.datetime.fromisoformat(date_in) if isinstance(date_in, str) else date_in
    return datetime.datetime.strftime(date, "%Y-%m-%d %H:%M:%S")


def pprint_date(date_in: str | datetime.datetime) -> str:
    """
    Pretty print a date.

    Short date with full date as title
    """
    if date_in == "None" or date_in is None:
        return "-"

    full_date = pprint_full_date(date_in)
    short_date = pprint_short_date(date_in)

    return f'<span title="{full_date}">{short_date}</span>'


def pprint_duration(duration_in: str | datetime.timedelta) -> str:
    """Pretty print a duration."""
    if duration_in == "None" or duration_in is None:
        return "-"

    if isinstance(duration_in, str):
        if " days, " in duration_in or " day, " in duration_in:
            day_txt = " days, " if " days, " in duration_in else " day, "
            day_, duration_in = duration_in.split(day_txt)
            day = int(day_)

            date = datetime.datetime.strptime(
                duration_in,
                "%H:%M:%S.%f" if "." in duration_in else "%H:%M:%S",
            ).replace(tzinfo=datetime.UTC)
        else:
            day = 0
            date = datetime.datetime.strptime(
                duration_in,
                "%H:%M:%S.%f" if "." in duration_in else "%H:%M:%S",
            ).replace(tzinfo=datetime.UTC)
        duration = datetime.timedelta(
            days=day,
            hours=date.hour,
            minutes=date.minute,
            seconds=date.second,
            microseconds=date.microsecond,
        )
    else:
        duration = duration_in

    secounds_abs = abs(duration.total_seconds())
    if secounds_abs < 60:
        plurial = "" if int(round(secounds_abs)) == 1 else "s"
        return f"{int(round(duration.total_seconds()))} second{plurial}"
    if secounds_abs < 3600:
        plurial = "" if int(round(secounds_abs / 60)) == 1 else "s"
        return f"{int(round(duration.total_seconds() / 60))} minute{plurial}"
    if secounds_abs < 86400:
        plurial = "" if int(round(secounds_abs / 3600)) == 1 else "s"
        return f"{int(round(duration.total_seconds() / 3600))} hour{plurial}"
    plurial = "" if int(round(secounds_abs / 86400)) == 1 else "s"
    return f"{int(round(duration.total_seconds() / 86400))} day{plurial}"
