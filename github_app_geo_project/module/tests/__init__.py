import json
import logging
import os
import subprocess
from typing import Any

import pygments.formatters
import pygments.lexers
import yaml

from github_app_geo_project import models, module
from github_app_geo_project.module import utils

_LOGGER = logging.getLogger(__name__)
_ConfigType = dict[str, Any]


class TestModule(module.Module[_ConfigType]):
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
        return [
            module.Action(priority=module.PRIORITY_STATUS, data={"type": "success"}),
            module.Action(priority=module.PRIORITY_STATUS, data={"type": "error"}),
            module.Action(priority=module.PRIORITY_STATUS, data={"type": "log-multiline"}),
            module.Action(priority=module.PRIORITY_STATUS, data={"type": "log-command"}),
            module.Action(priority=module.PRIORITY_STATUS, data={"type": "log-json"}),
        ]

    def process(self, context: module.ProcessContext[_ConfigType]) -> module.ProcessOutput | None:
        """
        Process the action.

        Note that this method is called in the queue consuming Pod

        :return: The status of the process to be stored in the dashboard issue
        """
        result = {}
        try:
            if os.path.exists("/tmp/test-result.yaml"):
                with open("/tmp/test-result.yaml", encoding="utf-8") as file:
                    result = yaml.load(file, Loader=yaml.SafeLoader)

            type_ = context.module_data.get("type", "-")
            result[f"{type_}-job-id"] = context.job_id

            if type_ == "error":
                result["error-job-id"] = context.job_id
                _LOGGER.debug("Debug")
                _LOGGER.info("Info")
                _LOGGER.warning("Warning")
                _LOGGER.error("Error")
                _LOGGER.critical("Critical")
                with open("/tmp/test-result.yaml", "w", encoding="utf-8") as file:
                    yaml.dump(result, file)
                raise Exception("Exception")  # pylint: disable=broad-exception-raised

            if type_ == "success":
                result["output-multi-line-id"] = utils.add_output(
                    context, "Test", ["Test 1", {"title": "Test 2", "children": ["Test 3", "Test 4"]}]
                )
                result["output-error-id"] = utils.add_output(
                    context, "Test", ["Test error"], status=models.OutputStatus.ERROR
                )

            if type_ == "log-multiline":
                _LOGGER.info("Line 1\n  Line 2\nLine 3")

            if type_ == "log-command":
                proc = subprocess.run(
                    ["echo", "-e", r"plain \e[0;31mRED MESSAGE\e[0m reset"],
                    capture_output=True,
                    encoding="utf-8",
                    check=True,
                )
                message = utils.ansi_proc_message(proc)
                _LOGGER.info(message.to_html())
                proc = subprocess.run(
                    ["echo", "-e", r"plain \e[0;31mRED MESSAGE\e[0m reset"],
                    capture_output=True,
                    encoding="utf-8",
                    check=True,
                )
                message = utils.ansi_proc_message(proc)
                message.title = "Command with title"
                _LOGGER.info(message.to_html(style="collapse"))

            if type_ == "log-json":
                lexer = pygments.lexers.JsonLexer()
                formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")
                _LOGGER.info(
                    "JSON output:\n%s",
                    pygments.highlight(
                        json.dumps({"test1": "value", "test2": "value"}, indent=4), lexer, formatter
                    ),
                )

            with open("/tmp/test-result.yaml", "w", encoding="utf-8") as file:
                yaml.dump(result, file)
            return None
        finally:
            with open("/results/test-result.yaml", "w", encoding="utf-8") as file:
                file.write(yaml.dump(result))

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
