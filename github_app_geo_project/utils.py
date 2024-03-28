"""Application utility module."""

from typing import Any

from github_app_geo_project import module

_ISSUE_START = "<!-- START {} -->"
_ISSUE_END = "<!-- END {} -->"


def get_dashboard_issue_module(text: str, current_module: str) -> str:
    """Get the part of the issue related to a module."""
    start_tag = _ISSUE_START.format(current_module)
    end_tag = _ISSUE_END.format(current_module)
    issue_data = ""
    if start_tag in text and end_tag in text:
        start = text.index(start_tag) + len(start_tag)
        end = text.index(end_tag)
        issue_data = text[start:end]
        issue_data = issue_data.strip()
        if issue_data.startswith("## "):
            issue_data = "\n".join(issue_data.split("\n")[1:]).strip()
    return issue_data


def update_dashboard_issue_module(
    text: str, module_name: str, current_module: module.Module[Any], data: str
) -> str:
    """Update the issue data (text) of a module with his new data."""
    start_tag = _ISSUE_START.format(module_name)
    end_tag = _ISSUE_END.format(module_name)
    issue_data = (
        "\n".join(
            [
                start_tag,
                f"## {current_module.title()}",
                "",
                data,
                end_tag,
            ]
        )
        if data
        else ""
    )
    if start_tag in text and end_tag in text:
        start = text.index(start_tag)
        end = text.index(end_tag) + len(end_tag)
        return text[:start] + issue_data + text[end:]
    else:
        return f"{text}{issue_data}"
