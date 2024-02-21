"""The base class of the modules."""

from abc import abstractmethod
from typing import Generic, NamedTuple, TypeVar, Union

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
# Standard priority
PRIORITY_STANDARD = 20
# Priority for an action triggered by a cron
PRIORITY_CRON = 30

# Json Type
Json = Union[int, float, str, None, dict[str, "Json"], list["Json"]]
JsonDict = dict[str, Json]

T = TypeVar("T")


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
    def get_actions(self, event_data: JsonDict) -> list[Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """

    def add_output(
        self,
        context: ProcessContext[T],
        title: str,
        data: list[Union[str, models.OutputData]],
        status: models.OutputStatus = models.OutputStatus.SUCCESS,
        access_type: models.AccessType = models.AccessType.PULL,
    ) -> None:
        """Add an output to the database."""
        context.session.add(
            models.Output(
                title=title,
                status=status,
                owner=context.owner,
                repository=context.repository,
                access_type=access_type,
                data=data,
            )
        )

    @abstractmethod
    def process(self, context: ProcessContext[T]) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """

    @abstractmethod
    def get_json_schema(self) -> JsonDict:
        """Get the JSON schema of the module configuration."""

    def required_issue_dashboard(self) -> bool:
        """Return True if the module requires the issue dashboard."""
        return False

    def get_github_application_permissions(self) -> GitHubApplicationPermissions:
        """Get the list of permissions needed by the GitHub application."""
        return GitHubApplicationPermissions({}, set())
