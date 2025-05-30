import asyncio
import logging
import os
import subprocess
from typing import Any

import yaml
from pydantic import BaseModel

from github_app_geo_project import models, module, utils
from github_app_geo_project.module import utils as module_utils

_LOGGER = logging.getLogger(__name__)


class _ConfigType(BaseModel):
    test: str = "by default"


class _EventData(BaseModel):
    type: str


class _TransversalDashboardData(BaseModel):
    content: str = "content by default"


class _IntermediaryStatus(BaseModel):
    pass


class TestModule(module.Module[_ConfigType, _EventData, _TransversalDashboardData, _IntermediaryStatus]):
    def title(self) -> str:
        """Get the title of the module."""
        return "Test Module"

    def description(self) -> str:
        """Get the description of the module."""
        return "This module is used to test the application"

    def documentation_url(self) -> str:
        """Get the URL to the documentation page of the module."""
        return ""

    def get_actions(self, context: module.GetActionContext) -> list[module.Action[_EventData]]:
        """
        Get the action related to the module and the event.

        Usually the only action allowed to be done in this method is to set the pull request checks status
        Note that this function is called in the web server Pod who has low resources, and this call should be fast
        """
        del context
        return [
            module.Action(priority=module.PRIORITY_STATUS, data=_EventData(type="success")),
            module.Action(priority=module.PRIORITY_STATUS, data=_EventData(type="error")),
            module.Action(priority=module.PRIORITY_STATUS, data=_EventData(type="log-multiline")),
            module.Action(priority=module.PRIORITY_STATUS, data=_EventData(type="log-command")),
            module.Action(priority=module.PRIORITY_STATUS, data=_EventData(type="log-json")),
        ]

    async def process(
        self,
        context: module.ProcessContext[_ConfigType, _EventData],
    ) -> module.ProcessOutput[_EventData, _TransversalDashboardData]:
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

            type_ = context.module_event_data.type
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
                result["output-multi-line-id"] = await module_utils.add_output(
                    context,
                    "Test",
                    ["Test 1", {"title": "Test 2", "children": ["Test 3", "Test 4"]}],
                )
                result["output-error-id"] = await module_utils.add_output(
                    context,
                    "Test",
                    ["Test error"],
                    status=models.OutputStatus.ERROR,
                )

            if type_ == "log-multiline":
                _LOGGER.info("Line 1\n  Line 2\nLine 3")

            if type_ == "log-command":
                command = ["echo", "-e", r"plain \e[0;31mRED MESSAGE\e[0m reset"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                )
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
                message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
                _LOGGER.info(message)
                command = ["echo", "-e", r"plain \e[0;31mRED MESSAGE\e[0m reset"]
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                )
                async with asyncio.timeout(60):
                    stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    raise subprocess.CalledProcessError(proc.returncode, command, stdout, stderr)
                message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
                message.title = "Command with title"
                _LOGGER.info(message)

            if type_ == "log-json":
                _LOGGER.info("JSON output:\n%s", utils.format_json({"test1": "value", "test2": "value"}))

            with open("/tmp/test-result.yaml", "w", encoding="utf-8") as file:
                yaml.dump(result, file)

            return module.ProcessOutput(updated_transversal_status=True)

        finally:
            with open("/results/test-result.yaml", "w", encoding="utf-8") as file:
                file.write(yaml.dump(result))

    async def update_transversal_status(
        self,
        context: module.ProcessContext[_ConfigType, _EventData],
        intermediary_status: _IntermediaryStatus,
        transversal_status: _TransversalDashboardData,
    ) -> _TransversalDashboardData:
        """
        Update the transversal status with the intermediary status.
        """
        del context, intermediary_status, transversal_status  # Unused
        return _TransversalDashboardData(content="<b>Some content</b>")

    async def get_json_schema(self) -> dict[str, Any]:
        """Get the JSON schema of the module configuration."""
        return {
            "type": "object",
            "properties": {
                "test": {
                    "type": "string",
                },
            },
        }

    def has_transversal_dashboard(self) -> bool:
        """Check if the module has a transversal dashboard."""
        return True

    def get_transversal_dashboard(
        self,
        context: module.TransversalDashboardContext[_TransversalDashboardData],
    ) -> module.TransversalDashboardOutput:
        return module.TransversalDashboardOutput(
            renderer="github_app_geo_project:module/tests/dashboard.html",
            data=context.status.model_dump(),
        )
