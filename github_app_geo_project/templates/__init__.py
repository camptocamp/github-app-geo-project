# Copyright (c) 2026, Camptocamp SA

"""The mako templates to render the pages."""

import datetime
import logging
from typing import Any

import anyio
import markdown as markdown_lib
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from github_app_geo_project.module.utils import _SANITIZER

_LOGGER = logging.getLogger(__name__)


def sanitizer(text: str) -> Markup:
    """Sanitize the input string."""
    return Markup(_SANITIZER.sanitize(text))  # noqa: S704 # nosec


def markdown(text: str | None) -> Markup:
    """Convert the input string to markdown."""
    if text is None:
        return Markup("")
    return sanitizer(markdown_lib.markdown(text))


def pprint_short_date(date: datetime.datetime | None) -> str:
    """Pretty print a short date (essentially time to current time)."""
    if date is None:
        return "-"

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


def pprint_full_date(date: datetime.datetime | None) -> str:
    """Pretty print a full date."""
    if date is None:
        return "-"

    return datetime.datetime.strftime(date, "%Y-%m-%d %H:%M:%S")


def pprint_date(date: datetime.datetime | None) -> Markup:
    """
    Pretty print a date.

    Short date with full date as title
    """
    if date is None:
        return Markup("-")

    full_date = pprint_full_date(date)
    short_date = pprint_short_date(date)

    return Markup('<span title="{}">{}</span>').format(full_date, short_date)


def pprint_duration(duration: datetime.timedelta | None) -> str:
    """Pretty print a duration."""
    if duration is None:
        return "-"

    seconds_abs = abs(duration.total_seconds())
    if seconds_abs < 60:
        plural = "" if round(seconds_abs) == 1 else "s"
        return f"{round(duration.total_seconds())} second{plural}"
    if seconds_abs < 3600:
        plural = "" if round(seconds_abs / 60) == 1 else "s"
        return f"{round(duration.total_seconds() / 60)} minute{plural}"
    if seconds_abs < 86400:
        plural = "" if round(seconds_abs / 3600) == 1 else "s"
        return f"{round(duration.total_seconds() / 3600)} hour{plural}"
    plural = "" if round(seconds_abs / 86400) == 1 else "s"
    return f"{round(duration.total_seconds() / 86400)} day{plural}"


async def render_template(renderer: str, data: dict[str, Any]) -> str:
    """Render a template from a renderer string in the format 'package:path'."""
    package, path = renderer.split(":", 1)
    package_dir = anyio.Path(__file__).parent.parent.parent / package
    template_path = package_dir / path
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(),
    )
    env.filters["markdown"] = markdown
    env.filters["sanitizer"] = sanitizer
    env.filters["pprint_date"] = pprint_date
    env.filters["pprint_short_date"] = pprint_short_date
    env.filters["pprint_full_date"] = pprint_full_date
    env.filters["pprint_duration"] = pprint_duration
    template = env.get_template(template_path.name)
    return template.render(data)
