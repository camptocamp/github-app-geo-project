"""Automatically generated file from a JSON schema."""

from typing import TypedDict

from typing_extensions import Required


class DeleteOldWorkflowRunsConfiguration(TypedDict, total=False):
    """Delete old workflow runs configuration."""

    rules: list["Rule"]


# | Rule.
# |
# | A rule to filter the list of workflow runs
Rule = TypedDict(
    "Rule",
    {
        # | Required property
        "older-than-days": Required[int],
        "workflow": str,
        "actor": str,
        "branch": str,
        "event": str,
        "status": str,
    },
    total=False,
)
