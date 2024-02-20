from typing import Any

from github_app_geo_project import models, module

ConfigType = dict[str, Any]


class TestModule(module.Module[ConfigType]):
    def title(self) -> str:
        """Get the title of the module."""
        return "Test Module"

    def description(self) -> str:
        """Get the description of the module."""
        return "This module is used to test the application"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return ""

    def get_actions(self, event_data: module.JsonDict) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        del event_data
        return [module.Action(priority=module.PRIORITY_STATUS)]

    def process(self, context: module.ProcessContext[ConfigType]) -> None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod
        """
        context.session.add(
            models.Output(
                title="Test",
                status=models.OutputStatus.SUCCESS,
                repository="camptocamp/github-app-geo-project",
                access_type=models.AccessType.PUBLIC,
                data=["Test 1", {"title": "Test 2", "children": ["Test 3", "Test 4"]}],
            )
        )
        context.session.add(
            models.Output(
                title="Test error",
                status=models.OutputStatus.ERROR,
                repository="camptocamp/github-app-geo-project",
                access_type=models.AccessType.PUBLIC,
                data=["Test error"],
            )
        )

    def get_json_schema(self) -> module.JsonDict:
        """Get the JSON schema of the module configuration."""
        return {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                }
            },
        }
