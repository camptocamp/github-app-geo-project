"""
Automatically generated file from a JSON schema.
"""


from typing import Literal, Required, TypedDict


class DeleteOldWorkflowRunsConfiguration(TypedDict, total=False):
    r""" Delete old workflow runs configuration. """

    rules: list["Rule"]


# | Rule.
# | 
# | A rule to filter the list of workflow runs
Rule = TypedDict('Rule', {
    # | Required property
    'older-than-days': Required[int],
    'workflow': str,
    'actor': str,
    'branch': str,
    'event': str,
    'status': "_RuleStatus",
}, total=False)


_RuleStatus = Literal['completed'] | Literal['action_required'] | Literal['cancelled'] | Literal['failure'] | Literal['neutral'] | Literal['skipped'] | Literal['stale'] | Literal['success'] | Literal['timed_out'] | Literal['in_progress'] | Literal['queued'] | Literal['requested'] | Literal['waiting'] | Literal['pending']
_RULESTATUS_COMPLETED: Literal['completed'] = "completed"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_ACTION_REQUIRED: Literal['action_required'] = "action_required"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_CANCELLED: Literal['cancelled'] = "cancelled"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_FAILURE: Literal['failure'] = "failure"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_NEUTRAL: Literal['neutral'] = "neutral"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_SKIPPED: Literal['skipped'] = "skipped"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_STALE: Literal['stale'] = "stale"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_SUCCESS: Literal['success'] = "success"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_TIMED_OUT: Literal['timed_out'] = "timed_out"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_IN_PROGRESS: Literal['in_progress'] = "in_progress"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_QUEUED: Literal['queued'] = "queued"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_REQUESTED: Literal['requested'] = "requested"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_WAITING: Literal['waiting'] = "waiting"
r"""The values for the '_RuleStatus' enum"""
_RULESTATUS_PENDING: Literal['pending'] = "pending"
r"""The values for the '_RuleStatus' enum"""

