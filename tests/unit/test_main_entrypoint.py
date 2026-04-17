"""Tests for the :mod:`ymca.__main__` entry point."""

from __future__ import annotations

import runpy

import pytest


def test_main_module_raises_system_exit_with_cli_return_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ymca.cli.main", lambda: 0)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("ymca", run_name="__main__")

    assert exc_info.value.code == 0
