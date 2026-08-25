# Copyright (c) 2026, Camptocamp SA

"""Tests for the process-queue health check script."""

import os
import time

import pytest

from github_app_geo_project.scripts import health_check


def _create_watch_dog(tmp_path, age):
    watch_dog = tmp_path / "watch_dog"
    watch_dog.write_text("", encoding="utf-8")
    old_time = time.time() - age
    os.utime(watch_dog, (old_time, old_time))
    return watch_dog


@pytest.mark.parametrize(
    ("age", "timeout"),
    [
        (0, 120),
        (30, 120),
        (60, 120),
    ],
)
def test_health_check_ok(tmp_path, monkeypatch, age, timeout) -> None:
    """A watchdog file updated recently enough must not fail the health check."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, age))
    monkeypatch.setattr(
        "sys.argv",
        ["health_check", f"--timeout={timeout}"],
    )
    health_check.main()


def test_health_check_warning_but_healthy(tmp_path, monkeypatch, capsys) -> None:
    """Between half of the timeout and the timeout, warn but do not fail."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 90))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    subprocess_calls = []
    monkeypatch.setattr(
        health_check.subprocess,
        "run",
        lambda *args, **kwargs: subprocess_calls.append(args),
    )

    health_check.main()

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "ERROR" not in out
    assert len(subprocess_calls) == 3


def test_health_check_fail(tmp_path, monkeypatch, capsys) -> None:
    """When the event loop is blocked for too long, the health check must fail."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 150))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    monkeypatch.setattr(health_check.subprocess, "run", lambda *args, **kwargs: None)

    with pytest.raises(SystemExit) as exc_info:
        health_check.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "ERROR" in out
    assert "unhealthy" in out


def test_health_check_timeout_required(monkeypatch) -> None:
    """The timeout argument is required."""
    monkeypatch.setattr("sys.argv", ["health_check"])
    with pytest.raises(SystemExit) as exc_info:
        health_check.main()
    assert exc_info.value.code == 2
