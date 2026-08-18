# Copyright (c) 2026, Camptocamp SA

import pytest

from github_app_geo_project.settings import ApplicationSettings


def test_priority_groups_default() -> None:
    """priority_groups should default to a single max-int group."""
    settings = ApplicationSettings()
    assert settings.process_queue.priority_groups == [2147483647]


def test_priority_groups_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """priority_groups should be parsed from a comma-separated environment variable."""
    monkeypatch.setenv("GHCI__PROCESS_QUEUE__PRIORITY_GROUPS", "1, 2,3")
    settings = ApplicationSettings()
    assert settings.process_queue.priority_groups == [1, 2, 3]


def test_priority_groups_single_value_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """priority_groups should accept a single value from the environment variable."""
    monkeypatch.setenv("GHCI__PROCESS_QUEUE__PRIORITY_GROUPS", "10")
    settings = ApplicationSettings()
    assert settings.process_queue.priority_groups == [10]
