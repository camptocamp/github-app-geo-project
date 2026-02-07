"""The base class of the modules."""

import json
import logging
from abc import abstractmethod
from collections.abc import Mapping
from enum import Enum
from types import GenericAlias
from typing import Any, Generic, Literal, NamedTuple, NotRequired, TypedDict, TypeVar

import sqlalchemy.ext.asyncio
from pydantic import BaseModel, ValidationError

from github_app_geo_project import configuration

_LOGGER = logging.getLogger(__name__)

PRIORITY_HIGH = 0
"""Priority used to preprocess the dashboard issue"""
PRIORITY_STATUS = 10
"""Priority used to update the pull request status"""
PRIORITY_DASHBOARD = 20
"""Priority used for actions triggered by dashboard issue"""
PRIORITY_STANDARD = 30
"""Standard priority"""
PRIORITY_CRON = 40
"""Priority for an action triggered by a cron"""

_CONFIGURATION = TypeVar("_CONFIGURATION")
"""The module configuration"""
_EVENT_DATA = TypeVar("_EVENT_DATA")  # pylint: disable=invalid-name
"""The module event data"""
_TRANSVERSAL_STATUS = TypeVar("_TRANSVERSAL_STATUS")  # pylint: disable=invalid-name
"""The module transversal status"""
_INTERMEDIATE_STATUS = TypeVar("_INTERMEDIATE_STATUS")  # pylint: disable=invalid-name
"""The module intermediate status"""


class GHCIError(Exception):
    """Exception raised when the module cannot be processed, consider that a pertinent error message is already logged."""


class Action(Generic[_EVENT_DATA]):
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
    data: _EVENT_DATA
    """Some data to be used by the process method"""
    checks: bool | None
    """If the action should add a pull request status"""

    def __init__(
        self,
        data: _EVENT_DATA,
        priority: int = -1,
        title: str = "",
        checks: bool | None = None,
    ) -> None:
        """Create an action."""
        self.title = title
        self.priority = priority
        self.data = data
        self.checks = checks


class GetActionContext(NamedTuple):
    """The context of the get_actions method."""

    github_event_name: str
    """The GitHub event name present in the X-GitHub-Event header."""
    github_event_data: Mapping[str, Any]
    """The GitHub event data."""
    module_event_name: str
    """The module event name."""
    owner: str
    """The owner of the event."""
    repository: str
    """The repository of the event."""
    github_application: configuration.GithubApplication
    """The github application."""


class CleanupContext(NamedTuple, Generic[_EVENT_DATA]):
    """The context of the cleanup method."""

    github_project: configuration.GithubProject
    """The github application."""
    github_event_name: str
    """The event name present in the X-GitHub-Event header."""
    github_event_data: dict[str, Any]
    """The event data."""
    module_event_name: str
    """The module event name."""
    module_event_data: _EVENT_DATA
    """The data given by the get_actions method."""


class ProcessContext(NamedTuple, Generic[_CONFIGURATION, _EVENT_DATA]):
    """
    The context provided to the process method of a module.

    This context contains all the information needed to process a module action,
    including database session, GitHub project information, event data, and configuration.

    Attributes
    ----------
        session: The async SQLAlchemy session to be used for database operations.
        github_project: The GitHub project information containing owner, repository,
            and authenticated GitHub API client (aio_github).
        github_event_name: The GitHub event name present in the X-GitHub-Event header
            (e.g., 'push', 'pull_request', 'issues', etc.).
        github_event_data: The complete GitHub event data as a dictionary.
        module_config: The module configuration of type _CONFIGURATION (typically a Pydantic model).
        module_event_name: The module-specific event name (e.g., 'dashboard', 'cron', etc.).
        module_event_data: The module event data of type _EVENT_DATA created by get_actions method.
        issue_data: The raw data from the issue dashboard (typically a markdown string).
        job_id: The unique job ID for this processing task.
        service_url: The base URL of the application service for generating links.

    Example:
        >>> async def process(self, context: ProcessContext) -> ProcessOutput:
        ...     # Access GitHub project information
        ...     owner = context.github_project.owner
        ...     repo = context.github_project.repository
        ...
        ...     # Access module configuration
        ...     config = context.module_config
        ...
        ...     # Access event data
        ...     event_type = context.module_event_data.type
        ...
        ...     # Use database session
        ...     result = await context.session.execute(...)
        ...
        ...     return ProcessOutput(success=True)
    """

    session: sqlalchemy.ext.asyncio.AsyncSession
    """The session to be used."""
    github_project: configuration.GithubProject
    """The github application."""
    github_event_name: str
    """The event name present in the X-GitHub-Event header."""
    github_event_data: dict[str, Any]
    """The event data."""
    module_config: _CONFIGURATION
    """The module configuration."""
    module_event_name: str
    """The module event name."""
    module_event_data: _EVENT_DATA
    """The data given by the get_actions method."""
    issue_data: str
    """The data from the issue dashboard."""
    job_id: int
    """The job ID."""
    service_url: str
    """The base URL of the application."""


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
    attestations: NotRequired[Literal["read", "write"]]
    checks: NotRequired[Literal["read", "write"]]
    code_scanning_alerts: NotRequired[Literal["read", "write"]]
    codespaces: NotRequired[Literal["read", "write"]]
    codespaces_lifecycle_admin: NotRequired[Literal["read", "write"]]
    codespaces_metadata: NotRequired[Literal["read", "write"]]
    codespaces_secrets: NotRequired[Literal["read", "write"]]
    commit_statuses: NotRequired[Literal["read", "write"]]
    contents: NotRequired[Literal["read", "write"]]
    dependabot_alerts: NotRequired[Literal["read", "write"]]
    dependabot_secrets: NotRequired[Literal["read", "write"]]
    deployments: NotRequired[Literal["read", "write"]]
    discussions: NotRequired[Literal["read", "write"]]
    environments: NotRequired[Literal["read", "write"]]
    issues: NotRequired[Literal["read", "write"]]
    merge_queues: NotRequired[Literal["read", "write"]]
    metadata: NotRequired[Literal["read", "write"]]
    pages: NotRequired[Literal["read", "write"]]
    pull_requests: NotRequired[Literal["read", "write"]]
    repository_security_advisories: NotRequired[Literal["read", "write"]]
    secret_scanning_alerts: NotRequired[Literal["read", "write"]]
    single_file: NotRequired[Literal["read", "write"]]
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


class ProcessOutput(Generic[_EVENT_DATA, _INTERMEDIATE_STATUS]):
    """The output of the process method."""

    dashboard: str | None
    """The transversal status of the module."""
    intermediate_status: _INTERMEDIATE_STATUS | None
    """The intermediate status of the module."""
    updated_transversal_status: bool
    """If the transversal status should be updated."""
    actions: list[Action[_EVENT_DATA]]
    """The new actions that should be done."""
    success: bool
    """The success of the process."""
    output: dict[str, Any] | None
    """The output of the process."""

    def __init__(
        self,
        dashboard: str | None = None,
        intermediate_status: _INTERMEDIATE_STATUS | None = None,
        updated_transversal_status: bool = False,
        actions: list[Action[_EVENT_DATA]] | None = None,
        success: bool = True,
        output: dict[str, Any] | None = None,
    ) -> None:
        """Create the output of the process method."""
        self.dashboard = dashboard
        self.intermediate_status = intermediate_status
        self.updated_transversal_status = updated_transversal_status
        self.actions = actions or []
        self.success = success
        self.output = output


class TransversalDashboardContext(NamedTuple, Generic[_TRANSVERSAL_STATUS]):
    """The context of the global dashboard."""

    status: _TRANSVERSAL_STATUS
    params: dict[str, str]


class TransversalDashboardOutput(NamedTuple):
    """The output of the module query on the transversal dashboard."""

    renderer: str
    data: dict[str, Any]


class Fields(Enum):
    """Fields enum."""

    PRIORITY = "priority"
    OWNER = "owner"
    REPOSITORY = "repository"
    GITHUB_EVENT_NAME = "github_event_name"
    MODULE_EVENT_NAME = "module_event_name"
    GITHUB_EVENT_DATA = "github_event_data"
    MODULE_EVENT_DATA = "module_event_data"


class Module(
    Generic[_CONFIGURATION, _EVENT_DATA, _TRANSVERSAL_STATUS, _INTERMEDIATE_STATUS],
):
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

    def jobs_unique_on(self) -> list[Fields] | None:
        """
        Return the list of fields that should be unique for the jobs.

        If not unique, the other jobs will be skipped.
        """
        return None

    @abstractmethod
    def get_actions(self, context: GetActionContext) -> list[Action[_EVENT_DATA]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """

    @abstractmethod
    async def process(
        self,
        context: ProcessContext[_CONFIGURATION, _EVENT_DATA],
    ) -> ProcessOutput[_EVENT_DATA, _INTERMEDIATE_STATUS]:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod

        :return: The message to be displayed in the issue dashboard, None for not changes, '' for no message
                 This is taken in account only if the method required_issue_dashboard return True.
        """

    async def update_transversal_status(
        self,
        context: ProcessContext[_CONFIGURATION, _EVENT_DATA],
        intermediate_status: _INTERMEDIATE_STATUS,
        transversal_status: _TRANSVERSAL_STATUS,
    ) -> _TRANSVERSAL_STATUS | None:
        """
        Update the transversal status.

        This method will not be called concurrently for the same module.
        """
        del context, intermediate_status, transversal_status
        return None

    def cleanup(self, context: CleanupContext[_EVENT_DATA]) -> None:
        """
        Cleanup the event.

        The get_actions method is called event if the module is not enabled.
        It the module is not enabled, the cleanup method is called in place of process to
        be able to cleanup eventual action done by get_actions.
        """
        del context

    @abstractmethod
    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        super_ = next(c for c in self.__class__.__orig_bases__ if c.__origin__ == Module)  # type: ignore[attr-defined] # pylint: disable=no-member
        generic_element = super_.__args__[0]
        # Is Pydantic BaseModel
        if not isinstance(generic_element, GenericAlias) and issubclass(
            generic_element,
            BaseModel,
        ):
            return generic_element.model_json_schema()  # type: ignore[no-any-return]
        raise NotImplementedError("The method get_json_schema should be implemented")

    def configuration_from_json(self, data: dict[str, Any]) -> _CONFIGURATION:
        """Create the configuration from the JSON data."""
        super_ = next(c for c in self.__class__.__orig_bases__ if c.__origin__ == Module)  # type: ignore[attr-defined] # pylint: disable=no-member
        generic_element = super_.__args__[0]
        # Is Pydantic BaseModel
        if not isinstance(generic_element, GenericAlias) and issubclass(
            generic_element,
            BaseModel,
        ):
            try:
                return generic_element(**data)  # type: ignore[no-any-return]
            except ValidationError:
                _LOGGER.error(  # noqa: TRY400
                    "Invalid configuration, try with empty configuration: %s",
                    data,
                )
                return generic_element()  # type: ignore[no-any-return]

        return data  # type: ignore[return-value]

    def event_data_from_json(self, data: dict[str, Any]) -> _EVENT_DATA:
        """Create the module event data from the JSON data."""
        super_ = next(c for c in self.__class__.__orig_bases__ if c.__origin__ == Module)  # type: ignore[attr-defined] # pylint: disable=no-member
        generic_element = super_.__args__[1]
        # Is Pydantic BaseModel
        if (not isinstance(generic_element, GenericAlias)) and issubclass(
            generic_element,
            BaseModel,
        ):
            try:
                return generic_element(**data)  # type: ignore[no-any-return]
            except ValidationError:
                _LOGGER.error(  # noqa: TRY400
                    "Invalid event data, try with empty event data: %s",
                    data,
                )
                return generic_element()  # type: ignore[no-any-return]
        return data  # type: ignore[return-value]

    def event_data_to_json(self, data: _EVENT_DATA) -> dict[str, Any]:
        """Create the JSON data from the module event data."""
        if isinstance(data, BaseModel):
            _LOGGER.debug("%s: Thread event_data as Pydantic model", self.title())
            return json.loads(data.model_dump_json(exclude_none=True))  # type: ignore[no-any-return]
        _LOGGER.debug("%s: Thread event_data as JSON", self.title())
        return data  # type: ignore[return-value]

    def transversal_status_from_json(
        self,
        data: dict[str, Any] | None,
    ) -> _TRANSVERSAL_STATUS:
        """Create the transversal status from the JSON data."""
        data = data or {}
        super_ = next(c for c in self.__class__.__orig_bases__ if c.__origin__ == Module)  # type: ignore[attr-defined] # pylint: disable=no-member
        generic_element = super_.__args__[2]
        # Is Pydantic BaseModel
        if not isinstance(generic_element, GenericAlias) and issubclass(
            generic_element,
            BaseModel,
        ):
            try:
                return generic_element(**data)  # type: ignore[no-any-return]
            except ValidationError:
                _LOGGER.exception(
                    "Invalid transversal status, try with empty transversal status: %s",
                    data,
                )
                return generic_element()  # type: ignore[no-any-return]
        return data  # type: ignore[return-value]

    def transversal_status_to_json(
        self,
        transversal_status: _TRANSVERSAL_STATUS,
    ) -> dict[str, Any]:
        """Create the JSON data from the transversal status."""
        if isinstance(transversal_status, BaseModel):
            _LOGGER.debug(
                "%s: Thread transversal_status ay Pydantic model",
                self.title(),
            )
            return json.loads(transversal_status.model_dump_json(exclude_none=True))  # type: ignore[no-any-return]
        _LOGGER.debug("%s: Thread transversal_status as JSON", self.title())
        return transversal_status  # type: ignore[return-value]

    def required_issue_dashboard(self) -> bool:
        """Return True if the module requires the issue dashboard."""
        return False

    def get_github_application_permissions(self) -> GitHubApplicationPermissions:
        """Get the list of permissions needed by the GitHub application."""
        return GitHubApplicationPermissions({}, set())

    def has_transversal_dashboard(self) -> bool:
        """Return True if the module has a transversal dashboard."""
        return False

    def get_transversal_dashboard(
        self,
        context: TransversalDashboardContext[_TRANSVERSAL_STATUS],
    ) -> TransversalDashboardOutput:
        """Get the transversal dashboard content."""
        del context
        # Basic implementation to avoid to implement the method in the module
        return TransversalDashboardOutput(renderer="", data={})
