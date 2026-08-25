# Copyright (c) 2026, Camptocamp SA

"""Tests for the process-queue health check script."""

import os
import time
from unittest.mock import MagicMock

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


def _mock_subprocess_run(calls):
    """Create a mock subprocess.run that records calls and returns appropriate results."""

    def mock_run(*args, **kwargs):
        calls.append((args, kwargs))
        result = MagicMock()
        cmd = args[0] if args else kwargs.get("args", [])
        if "pgrep" in cmd:
            result.returncode = 0
            result.stdout = "12345"
        elif "py-spy" in cmd:
            result.returncode = 0
        else:
            result.returncode = 0
        return result

    return mock_run


def test_health_check_warning_but_healthy(tmp_path, monkeypatch, capsys) -> None:
    """Between half of the timeout and the timeout, warn but do not fail."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 90))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    subprocess_calls = []
    monkeypatch.setattr(
        health_check.subprocess,
        "run",
        _mock_subprocess_run(subprocess_calls),
    )

    health_check.main()

    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "ERROR" not in out
    commands = [call[0][0] for call in subprocess_calls]
    assert any("ls" in cmd for cmd in commands)
    assert any("pgrep" in cmd for cmd in commands)
    assert any("py-spy" in cmd for cmd in commands)
    assert any("ps" in cmd for cmd in commands)


def test_health_check_warning_pyspy_fails(tmp_path, monkeypatch, capsys) -> None:
    """When py-spy fails, fall back to ps aux."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 90))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    subprocess_calls = []

    def mock_run(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        cmd = args[0] if args else kwargs.get("args", [])
        result = MagicMock()
        if "pgrep" in cmd:
            result.returncode = 0
            result.stdout = "12345"
        elif "py-spy" in cmd:
            result.returncode = 1
        else:
            result.returncode = 0
        return result

    monkeypatch.setattr(health_check.subprocess, "run", mock_run)

    health_check.main()

    out = capsys.readouterr().out
    assert "WARNING" in out
    commands = [call[0][0] for call in subprocess_calls]
    assert any("ps" in cmd for cmd in commands)


def test_health_check_warning_no_pid(tmp_path, monkeypatch, capsys) -> None:
    """When process-queue PID is not found, fall back to ps aux."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 90))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    subprocess_calls = []

    def mock_run(*args, **kwargs):
        subprocess_calls.append((args, kwargs))
        cmd = args[0] if args else kwargs.get("args", [])
        result = MagicMock()
        if "pgrep" in cmd:
            result.returncode = 1
            result.stdout = ""
        else:
            result.returncode = 0
        return result

    monkeypatch.setattr(health_check.subprocess, "run", mock_run)

    health_check.main()

    out = capsys.readouterr().out
    assert "WARNING" in out
    commands = [call[0][0] for call in subprocess_calls]
    assert any("ps" in cmd for cmd in commands)
    assert not any("py-spy" in cmd for cmd in commands)


def test_health_check_fail(tmp_path, monkeypatch, capsys) -> None:
    """When the event loop is blocked for too long, the health check must fail."""
    monkeypatch.setattr(health_check, "WATCH_DOG_FILE", _create_watch_dog(tmp_path, 150))
    monkeypatch.setattr("sys.argv", ["health_check", "--timeout=120"])
    monkeypatch.setattr(
        health_check.subprocess,
        "run",
        _mock_subprocess_run([]),
    )

    with pytest.raises(SystemExit) as exc_info:
        health_check.main()

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "WARNING" not in out
    assert "ERROR" in out
    assert "unhealthy" in out


def test_health_check_timeout_required(monkeypatch) -> None:
    """The timeout argument is required."""
    monkeypatch.setattr("sys.argv", ["health_check"])
    with pytest.raises(SystemExit) as exc_info:
        health_check.main()
    assert exc_info.value.code == 2
