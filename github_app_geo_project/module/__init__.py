"""The base class of the modules."""

from abc import abstractmethod
from typing import Generic, NamedTuple, TypeVar, Union


class Action(NamedTuple):
    """The action to be done by the module."""

    repository: str
    # The action priority usually
    # 10 for updating pull request status
    # 20 for standard action
    # 30 for actions triggered by a cron event
    priority: int


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


class Module(Generic[T]):
    """The base class of the modules."""

    @abstractmethod
    def get_actions(self, event_data: JsonDict) -> list[Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """

    @abstractmethod
    def process(self, module_config: T, action: Action, event_data: JsonDict) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
