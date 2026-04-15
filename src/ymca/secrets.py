from __future__ import annotations

import getpass
import os
from pathlib import Path

from .errors import SecretError

API_KEY_ENV_VAR = "YNAB_API_KEY"


def load_api_key(
    *,
    api_key_file: Path | None = None,
    prompt_if_missing: bool = True,
) -> str:
    env_value = os.getenv(API_KEY_ENV_VAR)
    if env_value and env_value.strip():
        return env_value.strip()

    if api_key_file is not None:
        try:
            file_value = api_key_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretError(
                f"Failed to read YNAB API key file at {api_key_file}: {exc.strerror or exc}"
            ) from exc

        if not file_value:
            raise SecretError(f"Configured YNAB API key file is empty: {api_key_file}")

        return file_value

    if not prompt_if_missing:
        raise SecretError(f"No YNAB API key found. Set {API_KEY_ENV_VAR}.")

    try:
        api_key = getpass.getpass(f"Enter {API_KEY_ENV_VAR}: ").strip()
    except Exception:
        api_key = input(f"Enter {API_KEY_ENV_VAR}: ").strip()

    if not api_key:
        raise SecretError(
            f"No YNAB API key found. Set {API_KEY_ENV_VAR} or enter it when prompted."
        )

    return api_key
