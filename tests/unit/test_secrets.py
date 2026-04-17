from __future__ import annotations

from pathlib import Path

import pytest

from ymca.errors import SecretError
from ymca.secrets import load_api_key


def test_load_api_key_reads_configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_file = tmp_path / "ynab_api_key"
    api_key_file.write_text("  secret-from-file  \n", encoding="utf-8")
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    assert load_api_key(api_key_file=api_key_file, prompt_if_missing=False) == "secret-from-file"


def test_load_api_key_prefers_environment_over_configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_file = tmp_path / "ynab_api_key"
    api_key_file.write_text("secret-from-file\n", encoding="utf-8")
    monkeypatch.setenv("YNAB_API_KEY", "secret-from-env")

    assert load_api_key(api_key_file=api_key_file, prompt_if_missing=False) == "secret-from-env"


def test_load_api_key_raises_for_missing_configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_file = tmp_path / "missing_key"
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    with pytest.raises(SecretError, match="Failed to read YNAB API key file"):
        load_api_key(api_key_file=missing_file, prompt_if_missing=False)


def test_load_api_key_raises_for_empty_configured_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api_key_file = tmp_path / "ynab_api_key"
    api_key_file.write_text("   \n", encoding="utf-8")
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    with pytest.raises(SecretError, match="is empty"):
        load_api_key(api_key_file=api_key_file, prompt_if_missing=False)


def test_load_api_key_raises_when_no_sources_and_prompt_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    with pytest.raises(SecretError, match="No YNAB API key found"):
        load_api_key(prompt_if_missing=False)


def test_load_api_key_prompts_via_getpass_when_no_sources_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    monkeypatch.setattr("ymca.secrets.getpass.getpass", lambda prompt: "  prompted-secret  ")

    assert load_api_key() == "prompted-secret"


def test_load_api_key_falls_back_to_input_when_getpass_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)

    def _getpass_boom(prompt: str) -> str:
        raise RuntimeError("no tty")

    monkeypatch.setattr("ymca.secrets.getpass.getpass", _getpass_boom)
    monkeypatch.setattr("builtins.input", lambda prompt: "  fallback-secret  ")

    assert load_api_key() == "fallback-secret"


def test_load_api_key_raises_when_prompt_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YNAB_API_KEY", raising=False)
    monkeypatch.setattr("ymca.secrets.getpass.getpass", lambda prompt: "   ")

    with pytest.raises(SecretError, match="No YNAB API key found"):
        load_api_key()
