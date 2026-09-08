# Copyright (c) 2026, Camptocamp SA

"""Tests for the cache_clean module."""

from pathlib import Path

import anyio
import pytest

from github_app_geo_project.module.cache_clean import _get_cache_configs


def _get_config_path(label: str, home: Path) -> str:
    """Get the path of the cache config with the given label."""
    configs = _get_cache_configs(anyio.Path(str(home)))
    return str(next(config.path for config in configs if config.label == label))


def test_pyenv_cache_uses_pyenv_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The pyenv cache path should be based on PYENV_ROOT, like pyenv itself."""
    monkeypatch.setenv("PYENV_ROOT", str(tmp_path / "opt-pyenv"))

    assert _get_config_path("pyenv cache", tmp_path) == str(tmp_path / "opt-pyenv" / "cache")


def test_pyenv_cache_defaults_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Without PYENV_ROOT the pyenv cache path should default to `$HOME/.pyenv/cache`."""
    monkeypatch.delenv("PYENV_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert _get_config_path("pyenv cache", tmp_path) == str(tmp_path / ".pyenv" / "cache")


def test_home_based_caches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The other caches should be based on the home directory."""
    monkeypatch.setenv("PYENV_ROOT", str(tmp_path / "opt-pyenv"))
    home = tmp_path / "home"

    assert _get_config_path("pip", home) == str(home / ".cache" / "pip")
    assert _get_config_path("poetry artifacts", home) == str(home / ".cache" / "pypoetry" / "artifacts")
    assert _get_config_path("poetry virtualenvs", home) == str(home / ".cache" / "pypoetry" / "virtualenvs")
    assert _get_config_path("prek", home) == str(home / ".cache" / "prek")
    assert _get_config_path("npm", home) == str(home / ".npm")
