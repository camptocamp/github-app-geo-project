from typing import Any

import pytest
from pydantic import BaseModel

from github_app_geo_project import module
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


class ConfigDictModule(module.Module[dict[str, Any], None, None]):
    pass


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"type": "success"},
    ],
)
def test_json_configuration_from_json(data) -> None:
    test_module = ConfigDictModule()

    assert test_module.configuration_from_json(data) == data


class EventDataDictModule(module.Module[None, dict[str, Any], None]):
    pass


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"type": "success"},
    ],
)
def test_json_event_data_from_json(data) -> None:
    test_module = EventDataDictModule()

    assert test_module.event_data_from_json(data) == data


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"type": "success"},
    ],
)
def test_json_envent_data_to_json(data) -> None:
    test_module = EventDataDictModule()

    assert test_module.event_data_to_json(data) == data


class TransversalStatusDictModule(module.Module[None, None, dict[str, Any]]):
    pass


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"type": "success"},
    ],
)
def test_json_transversal_status_from_json(data) -> None:
    test_module = TransversalStatusDictModule()

    assert test_module.transversal_status_from_json(data) == data


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"type": "success"},
    ],
)
def test_json_transversal_status_to_json(data) -> None:
    test_module = TransversalStatusDictModule()

    assert test_module.transversal_status_to_json(data) == data


class Data(BaseModel):
    value: str


class ConfigDataModule(module.Module[Data, None, None]):
    pass


@pytest.mark.parametrize(
    "data,expected",
    [
        [{"value": "test"}, Data(value="test")],
    ],
)
def test_data_configuration_from_json(data, expected) -> None:
    test_module = ConfigDataModule()

    assert test_module.configuration_from_json(data) == expected


class EventDataDataModule(module.Module[None, Data, None]):
    pass


@pytest.mark.parametrize(
    "data,expected",
    [
        [{"value": "test"}, Data(value="test")],
    ],
)
def test_data_event_data_from_json(data, expected) -> None:
    test_module = EventDataDataModule()

    assert test_module.event_data_from_json(data) == expected


@pytest.mark.parametrize("data,expected", [[Data(value="test"), {"value": "test"}]])
def test_data_event_data_to_json(data, expected) -> None:
    test_module = EventDataDataModule()

    assert test_module.event_data_to_json(data) == expected


class TransversalStatusDataModule(module.Module[None, None, Data]):
    pass


@pytest.mark.parametrize(
    "data,expected",
    [
        [{"value": "test"}, Data(value="test")],
    ],
)
def test_data_transversal_status_from_json(data, expected) -> None:
    test_module = TransversalStatusDataModule()

    assert test_module.transversal_status_from_json(data) == expected


@pytest.mark.parametrize("data,expected", [[Data(value="test"), {"value": "test"}]])
def test_data_transversal_status_to_json(data, expected) -> None:
    test_module = TransversalStatusDataModule()

    assert test_module.transversal_status_to_json(data) == expected
