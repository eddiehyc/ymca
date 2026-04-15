from __future__ import annotations

from pathlib import Path

import pytest

from ymca.config import load_config
from ymca.errors import ConfigError


def test_load_config_parses_valid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """version: 1
secrets:
  api_key_file: ./secrets/ynab_api_key
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts:
  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert config.plan.alias == "personal"
    assert config.plan.base_currency == "USD"
    assert config.secrets.api_key_file == (tmp_path / "secrets" / "ynab_api_key").resolve()
    assert config.plan.accounts[0].alias == "travel_hkd"
    assert config.plan.fx_rates["HKD"].rate_text == "7.8"
    assert config.plan.fx_rates["HKD"].divide_to_base is True


def test_load_config_rejects_fx_rate_not_above_one(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """version: 1
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts:
  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "1"
    divide_to_base: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="greater than 1"):
        load_config(config_path)
