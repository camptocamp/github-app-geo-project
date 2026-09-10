# Copyright (c) 2026, Camptocamp SA

import pytest

from github_app_geo_project.settings import ApplicationSettings, parse_si_unit


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (1000, 1000.0),
        (1.5, 1.5),
        ("1000", 1000.0),
        ("1000B", 1000.0),
        ("1000o", 1000.0),
        ("0.5k", 500.0),
        ("1Ki", 1024.0),
        ("1000M", 1_000_000_000.0),
        ("1.5G", 1_500_000_000.0),
        ("2Go", 2_000_000_000.0),
        ("500MiB", 524_288_000.0),
        ("2GiB", 2 * 1024.0**3),
        ("1T", 1_000_000_000_000.0),
    ],
)
def test_parse_si_unit(text: str | float, expected: float) -> None:
    """parse_si_unit should handle decimal (1000) and binary (1024) prefixes."""
    assert parse_si_unit(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "1.5.5G", "1 MM", "-1M"])
def test_parse_si_unit_invalid(text: str) -> None:
    """parse_si_unit should raise ValueError on invalid input."""
    with pytest.raises(ValueError, match="Invalid SI unit"):
        parse_si_unit(text)


def test_si_unit_default_values() -> None:
    """The SiUnit settings defaults should use decimal (1000) multipliers."""
    settings = ApplicationSettings()
    assert settings.cache_clean.pip_max_size == 1_000_000_000.0
    assert settings.versions.renovate_graph_max_old_space_size == 3_000_000_000.0
