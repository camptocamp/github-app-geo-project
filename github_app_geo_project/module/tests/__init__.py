from typing import Any

from sqlalchemy.orm import Session

from github_app_geo_project import models, modules

ConfigType = dict[str, Any]


class TestModuel(modules.Module[ConfigType]):
    def title(self) -> str:
        """Get the title of the module."""
        return "Test Module"

    def description(self) -> str:
        """Get the description of the module."""
        return "This module is used to test the application"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return ""

    def get_actions(self, event_data: modules.JsonDict) -> list[modules.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        del event_data
        return [modules.Action(priority=modules.PRIORITY_STATUS)]

    def process(self, session: Session, module_config: ConfigType, event_data: modules.JsonDict) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        del module_config
        del event_data

        session.add(
            models.Output(
                title="Test", data=["Test 1", {"title": "Test 2", "children": ["Test 3", "Test 4"]}]
            )
        )

    def get_json_schema(self) -> modules.JsonDict:
        """Get the JSON schema of the module configuration."""
        return {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                }
            },
        }
