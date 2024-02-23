"""The base class of the modules."""

from abc import abstractmethod
from typing import Generic, NamedTuple, Optional, TypeVar, Union

import github
from sqlalchemy.orm import Session

from github_app_geo_project import models


class Action(NamedTuple):
    """The action to be done by the module."""

    # The action priority usually
    # 10 for updating pull request status
    # 20 for standard action
    # 30 for actions triggered by a cron event
    priority: int
    # Some data to be used by the process method
    data: dict[str, "Json"]


# Priority used to update the pull request status
PRIORITY_STATUS = 10
# Priority used for actions triggered by dashborad issue
PRIORITY_DASHBOARD = 20
# Standard priority
PRIORITY_STANDARD = 30
# Priority for an action triggered by a cron
PRIORITY_CRON = 40

# Json Type
Json = Union[int, float, str, None, dict[str, "Json"], list["Json"]]
JsonDict = dict[str, Json]

T = TypeVar("T")


class GetActionContext(NamedTuple):
    """The context of the get_actions method."""

    # The owner and repository of the event
    owner: str
    # The repository name of the event
    repository: str
    # The event data
    event_data: dict[str, "Json"]


class CleanupContext(NamedTuple):
    """The context of the cleanup method."""

    # The github application
    github_application: github.Github
    # The owner and repository of the event
    owner: str
    # The repository name of the event
    repository: str
    # The event data
    event_data: dict[str, "Json"]
    # The data given by the get_actions method
    module_data: dict[str, "Json"]


class ProcessContext(NamedTuple, Generic[T]):
    """The context of the process."""

    # The session to be used
    session: Session
    # The github application
    github_application: github.Github
    # The owner and repository of the event
    owner: str
    # The repository name of the event
    repository: str
    # The event data
    event_data: dict[str, "Json"]
    # The module configuration
    module_config: "T"
    # The data given by the get_actions method
    module_data: dict[str, "Json"]
    # The data from the issue dashboard
    issue_data: str


class GitHubApplicationPermissions(NamedTuple):
    """The permissions needed by the GitHub application."""

    permissions: dict[str, str]
    events: set[str]


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
    def process(self, context: ProcessContext[T]) -> Optional[str]:
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
    def get_json_schema(self) -> JsonDict:
        """Get the JSON schema of the module configuration."""

    def required_issue_dashboard(self) -> bool:
        """Return True if the module requires the issue dashboard."""
        return False

    def get_github_application_permissions(self) -> GitHubApplicationPermissions:
        """Get the list of permissions needed by the GitHub application."""
        return GitHubApplicationPermissions({}, set())
