"""Unit tests for :mod:`ymca.paths`.

Covers both env-var overrides and the XDG default paths without touching the
real filesystem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ymca.paths import (
    CONFIG_PATH_ENV_VAR,
    STATE_PATH_ENV_VAR,
    default_config_path,
    default_state_path,
)


def test_default_config_path_honors_env_var_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom" / "config.yaml"
    monkeypatch.setenv(CONFIG_PATH_ENV_VAR, str(override))

    assert default_config_path() == override


def test_default_config_path_uses_xdg_config_home_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_PATH_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg_config"))

    resolved = default_config_path()

    assert resolved == tmp_path / "xdg_config" / "ymca" / "config.yaml"


def test_default_config_path_falls_back_to_home_config_dot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(CONFIG_PATH_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = default_config_path()

    assert resolved == tmp_path / "home" / ".config" / "ymca" / "config.yaml"


def test_default_state_path_honors_env_var_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    override = tmp_path / "custom" / "state.yaml"
    monkeypatch.setenv(STATE_PATH_ENV_VAR, str(override))

    assert default_state_path() == override


def test_default_state_path_uses_xdg_state_home_when_no_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(STATE_PATH_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg_state"))

    resolved = default_state_path()

    assert resolved == tmp_path / "xdg_state" / "ymca" / "state.yaml"


def test_default_state_path_falls_back_to_home_local_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(STATE_PATH_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    resolved = default_state_path()

    assert resolved == tmp_path / "home" / ".local" / "state" / "ymca" / "state.yaml"
