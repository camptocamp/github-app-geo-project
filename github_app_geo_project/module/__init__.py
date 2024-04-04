"""The base class of the modules."""

from abc import abstractmethod
from collections.abc import Mapping
from typing import Any, Generic, Literal, NamedTuple, NotRequired, TypedDict, TypeVar

import github
from sqlalchemy.orm import Session

from github_app_geo_project import configuration, models


class Action:
    """The action to be done by the module."""

    title: str
    """Title"""
    priority: int
    """
    The action priority usually
    10 for updating pull request status
    20 for standard action
    30 for actions triggered by a cron event
    """
    data: Mapping[str, Any]
    """Some data to be used by the process method"""

    def __init__(
        self,
        priority: int,
        data: Mapping[str, Any],
        title: str = "",
    ) -> None:
        """Create an action."""
        self.title = title
        self.priority = priority
        self.data = data


# Priority used to preprocess the dashboard issue
PRIORITY_HIGH = 0
# Priority used to update the pull request status
PRIORITY_STATUS = 10
# Priority used for actions triggered by dashborad issue
PRIORITY_DASHBOARD = 20
# Standard priority
PRIORITY_STANDARD = 30
# Priority for an action triggered by a cron
PRIORITY_CRON = 40

T = TypeVar("T")


class GetActionContext(NamedTuple):
    """The context of the get_actions method."""

    # The event name present in the X-GitHub-Event header
    event_name: str
    # The event data
    event_data: dict[str, Any]
    # The owner of the event
    owner: str
    # The repository of the event
    repository: str


class CleanupContext(NamedTuple):
    """The context of the cleanup method."""

    # The github application
    github_project: configuration.GithubProject
    # The event name present in the X-GitHub-Event header
    event_name: str
    # The event data
    event_data: dict[str, Any]
    # The data given by the get_actions method
    module_data: dict[str, Any]


class ProcessContext(NamedTuple, Generic[T]):
    """The context of the process."""

    # The session to be used
    session: Session
    # The github application
    github_project: configuration.GithubProject
    # The event name present in the X-GitHub-Event header
    event_name: str
    # The event data
    event_data: dict[str, Any]
    # The module configuration
    module_config: "T"
    # The data given by the get_actions method
    module_data: dict[str, Any]
    # The data from the issue dashboard
    issue_data: str
    # The module status
    transversal_status: dict[str, Any]


class Permissions(TypedDict):
    """The permissions needed by the GitHub application."""

    # Organization or repository permissions
    administration: NotRequired[Literal["read", "write"]]
    custom_properties: NotRequired[Literal["read", "write"]]
    projects: NotRequired[Literal["read", "write"]]
    secrets: NotRequired[Literal["read", "write"]]
    variables: NotRequired[Literal["read", "write"]]
    webhooks: NotRequired[Literal["read", "write"]]
    # Organization permissions
    blocking_users: NotRequired[Literal["read", "write"]]
    custom_organization_roles: NotRequired[Literal["read", "write"]]
    events: NotRequired[Literal["read", "write"]]
    github_copilot_business: NotRequired[Literal["read", "write"]]
    members: NotRequired[Literal["read", "write"]]
    organization_codespaces_secrets: NotRequired[Literal["read", "write"]]
    organization_codespaces_settings: NotRequired[Literal["read", "write"]]
    organization_codespaces: NotRequired[Literal["read", "write"]]
    organization_dependabot_secrets: NotRequired[Literal["read", "write"]]
    personal_access_token_requests: NotRequired[Literal["read", "write"]]
    personal_access_tokens: NotRequired[Literal["read", "write"]]
    self_hosted_runners: NotRequired[Literal["read", "write"]]
    team_discussions: NotRequired[Literal["read", "write"]]
    # Repository permissions
    actions: NotRequired[Literal["read", "write"]]
    checks: NotRequired[Literal["read", "write"]]
    code_scanning_alerts: NotRequired[Literal["read", "write"]]
    codespaces_lifecycle_admin: NotRequired[Literal["read", "write"]]
    codespaces_metadata: NotRequired[Literal["read", "write"]]
    codespaces_secrets: NotRequired[Literal["read", "write"]]
    codespaces: NotRequired[Literal["read", "write"]]
    commit_statuses: NotRequired[Literal["read", "write"]]
    contents: NotRequired[Literal["read", "write"]]
    dependabot_alerts: NotRequired[Literal["read", "write"]]
    dependabot_secrets: NotRequired[Literal["read", "write"]]
    deployments: NotRequired[Literal["read", "write"]]
    environments: NotRequired[Literal["read", "write"]]
    issues: NotRequired[Literal["read", "write"]]
    metadata: NotRequired[Literal["read", "write"]]
    pages: NotRequired[Literal["read", "write"]]
    pull_requests: NotRequired[Literal["read", "write"]]
    repository_security_advisories: NotRequired[Literal["read", "write"]]
    secret_scanning_alerts: NotRequired[Literal["read", "write"]]
    workflows: NotRequired[Literal["read", "write"]]
    # User permissions
    block_another_user: NotRequired[Literal["read", "write"]]
    codespaces_user_secrets: NotRequired[Literal["read", "write"]]
    email_addresses: NotRequired[Literal["read", "write"]]
    followers: NotRequired[Literal["read", "write"]]
    gpg_keys: NotRequired[Literal["read", "write"]]
    gists: NotRequired[Literal["read", "write"]]
    git_ssh_keys: NotRequired[Literal["read", "write"]]
    interaction_limits: NotRequired[Literal["read", "write"]]
    notifications: NotRequired[Literal["read", "write"]]
    plan: NotRequired[Literal["read", "write"]]
    profile: NotRequired[Literal["read", "write"]]
    ssh_signing_keys: NotRequired[Literal["read", "write"]]
    starring: NotRequired[Literal["read", "write"]]
    watching: NotRequired[Literal["read", "write"]]


class GitHubApplicationPermissions(NamedTuple):
    """The permissions needed by the GitHub application."""

    # https://docs.github.com/fr/rest/authentication/permissions-required-for-github-apps?apiVersion=2022-11-28
    permissions: Permissions
    # https://docs.github.com/fr/webhooks/webhook-events-and-payloads
    events: set[
        Literal[
            "branch_protection_configuration",
            "branch_protection_rule",
            "check_run",
            "check_suite",
            "code_scanning_alert",
            "commit_comment",
            "create",
            "custom_property",
            "custom_property_values",
            "delete",
            "dependabot_alert",
            "deploy_key",
            "deployment",
            "deployment_protection_rule",
            "deployment_review",
            "deployment_status",
            "discussion",
            "discussion_comment",
            "fork",
            "github_app_authorization",
            "gollum",
            "installation",
            "installation_repositories",
            "installation_target",
            "issue_comment",
            "issues",
            "label",
            "marketplace_purchase",
            "member",
            "membership",
            "merge_group",
            "meta",
            "milestone",
            "org_block",
            "organization",
            "package",
            "page_build",
            "personal_access_token_request",
            "ping",
            "project_card",
            "project",
            "project_column",
            "projects_v2",
            "projects_v2_item",
            "public",
            "pull_request",
            "pull_request_review_comment",
            "pull_request_review",
            "pull_request_review_thread",
            "push",
            "registry_package",
            "release",
            "repository_advisory",
            "repository",
            "repository_dispatch",
            "repository_import",
            "repository_ruleset",
            "repository_vulnerability_alert",
            "secret_scanning_alert",
            "secret_scanning_alert_location",
            "security_advisory",
            "security_and_analysis",
            "sponsorship",
            "star",
            "status",
            "team_add",
            "team",
            "watch",
            "workflow_dispatch",
            "workflow_job",
            "workflow_run",
        ]
    ]


class ProcessOutput:
    """The output of the process method."""

    dashboard: str | None
    """The dashboard issue content."""
    transversal_status: dict[str, Any] | None
    """The transversal status of the module."""
    actions: list[Action]
    """The new actions that should be done."""
    log: str | None
    """The log of the process."""

    def __init__(
        self,
        dashboard: str | None = None,
        transversal_status: dict[str, Any] | None = None,
        actions: list[Action] | None = None,
        log: str | None = None,
    ) -> None:
        """Create the output of the process method."""
        self.dashboard = dashboard
        self.transversal_status = transversal_status
        self.actions = actions or []
        self.log = log


class TransversalDashboardContext(NamedTuple):
    """The context of the global dashboard."""

    status: dict[str, Any]
    params: dict[str, str]


class TransversalDashboardOutput(NamedTuple):
    """The output of the module query on the transversal dashboard."""

    renderer: str
    data: dict[str, Any]


class Module(Generic[T]):
    """The base class of the modules."""

    @abstractmethod
    def title(self) -> str:
        """Get the title of the module."""

    @abstractmethod
    def description(self) -> str:
        """Get the description of the module."""

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return ""

    @abstractmethod
    def get_actions(self, context: GetActionContext) -> list[Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """

    @abstractmethod
    def process(self, context: ProcessContext[T]) -> ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod

        :return: The message to be displayed in the issue dashboard, None for not changes, '' for no message
                 This is taken in account only if the method required_issue_dashboard return True.
        """

    def cleanup(self, context: CleanupContext) -> None:
        """
        Cleanup the event.

        The get_actions method is called event if the module is not enabled.
        It the module is not enabled, the cleanup method is called in place of process to
        be able to cleanup eventual action done by get_actions.
        """
        del context

    @abstractmethod
    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""

    def required_issue_dashboard(self) -> bool:
        """Return True if the module requires the issue dashboard."""
        return False

    def get_github_application_permissions(self) -> GitHubApplicationPermissions:
        """Get the list of permissions needed by the GitHub application."""
        return GitHubApplicationPermissions({}, set())

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False

    def get_transversal_dashboard(self, context: TransversalDashboardContext) -> TransversalDashboardOutput:
        """Get the transversal dashboard content."""
        del context
        # Basic implementation to avoid to implement the method in the module
        return TransversalDashboardOutput(renderer="", data={})
