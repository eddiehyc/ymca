from __future__ import annotations

import os
from pathlib import Path

CONFIG_PATH_ENV_VAR = "YMCA_CONFIG_PATH"
STATE_PATH_ENV_VAR = "YMCA_STATE_PATH"


def default_config_path() -> Path:
    configured = os.getenv(CONFIG_PATH_ENV_VAR)
    if configured:
        return Path(configured).expanduser()

    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    return base / "ymca" / "config.yaml"


def default_state_path() -> Path:
    configured = os.getenv(STATE_PATH_ENV_VAR)
    if configured:
        return Path(configured).expanduser()

    xdg_state_home = os.getenv("XDG_STATE_HOME")
    base = Path(xdg_state_home).expanduser() if xdg_state_home else Path.home() / ".local" / "state"
    return base / "ymca" / "state.yaml"
