from __future__ import annotations

from pathlib import Path

import pytest

from ymca.errors import SecretError
from ymca.secrets import load_api_key


def test_load_api_key_reads_configured_file(tmp_path: Path) -> None:
    api_key_file = tmp_path / "ynab_api_key"
    api_key_file.write_text("  secret-from-file  \n", encoding="utf-8")

    assert load_api_key(api_key_file=api_key_file, prompt_if_missing=False) == "secret-from-file"


def test_load_api_key_prefers_environment_over_configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_file = tmp_path / "ynab_api_key"
    api_key_file.write_text("secret-from-file\n", encoding="utf-8")
    monkeypatch.setenv("YNAB_API_KEY", "secret-from-env")

    assert load_api_key(api_key_file=api_key_file, prompt_if_missing=False) == "secret-from-env"


def test_load_api_key_raises_for_missing_configured_file(tmp_path: Path) -> None:
    missing_file = tmp_path / "missing_key"

    with pytest.raises(SecretError, match="Failed to read YNAB API key file"):
        load_api_key(api_key_file=missing_file, prompt_if_missing=False)
