from pydantic import BaseModel

from github_app_geo_project.module.modules import MODULES
from github_app_geo_project.module.tests import TestModule


def test_conversions() -> None:
    test_module = TestModule()

    config = test_module.configuration_from_json({})
    assert isinstance(config, BaseModel)
    assert config.test == "by default"

    event = test_module.event_data_from_json({"type": "success"})
    assert isinstance(event, BaseModel)

    result = test_module.event_data_to_json(event)
    assert result == {"type": "success"}

    status = test_module.transversal_status_from_json({})
    assert isinstance(status, BaseModel)
    status.content = {}

    result = test_module.transversal_status_to_json(status)
    assert result == {"content": {}}


def test_all_empty_status() -> None:
    for module in MODULES.values():
        module.transversal_status_to_json(module.transversal_status_from_json({}))
