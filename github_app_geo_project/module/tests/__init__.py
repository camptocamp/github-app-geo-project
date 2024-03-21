from typing import Any

from github_app_geo_project import models, module
from github_app_geo_project.module import utils

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

    def get_actions(self, context: module.GetActionContext) -> list[module.Action]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        del context
        return [module.Action(priority=module.PRIORITY_STATUS, data={})]

    def process(self, context: module.ProcessContext[ConfigType]) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod

        :return: The status of the process to be stored in the dashboard issue
        """
        utils.add_output(context, "Test", ["Test 1", {"title": "Test 2", "children": ["Test 3", "Test 4"]}])
        utils.add_output(context, "Test", ["Test error"], status=models.OutputStatus.ERROR)
        return None

    def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        return {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                }
            },
        }

    def has_transversal_dashboard(self) -> bool:
        return True

    def get_transversal_dashboard(
        self, context: module.TransversalDashboardContext
    ) -> module.TransversalDashboardOutput:
        del context
        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/tests/dashboard.html",
            data={
                # Content with HTML tag to see if they are escaped
                "content": "<b>Some content</b>",
            },
        )
