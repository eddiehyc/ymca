from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from ymca.config import load_config, write_config_template
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


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_VALID_CONFIG_HEAD = """version: 1
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts:
"""


def test_load_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_config(tmp_path / "nope.yaml")


def test_load_config_invalid_yaml(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "plans: [unterminated\n")
    with pytest.raises(ConfigError, match="Failed to parse config YAML"):
        load_config(path)


def test_load_config_rejects_non_mapping_root(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "- item\n")
    with pytest.raises(ConfigError, match="config must be a mapping"):
        load_config(path)


def test_load_config_rejects_wrong_version(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "version: 99\n")
    with pytest.raises(ConfigError, match="Unsupported config version"):
        load_config(path)


def test_load_config_rejects_non_integer_version(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "version: 'one'\n")
    with pytest.raises(ConfigError, match="version must be an integer"):
        load_config(path)


def test_load_config_rejects_missing_plan(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "version: 1\n")
    with pytest.raises(ConfigError, match="plan must be a mapping"):
        load_config(path)


def test_load_config_rejects_invalid_currency_code(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: hkd!
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="must be a 3-letter currency code"):
        load_config(path)


def test_load_config_rejects_duplicate_account_names(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
  travel_hkd_other:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="Duplicate configured account name"):
        load_config(path)


def test_load_config_rejects_empty_accounts(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """version: 1
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts: {}
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="At least one account must be configured"):
        load_config(path)


def test_load_config_rejects_all_accounts_disabled(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: false
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="At least one account must be enabled"):
        load_config(path)


def test_load_config_rejects_account_in_base_currency(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  usd_account:
    name: USD Account
    currency: USD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="v1 only supports foreign-currency accounts"):
        load_config(path)


def test_load_config_rejects_account_with_no_fx_entry(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_eur:
    name: Travel EUR
    currency: EUR
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="No fx_rates entry"):
        load_config(path)


def test_load_config_rejects_non_decimal_rate(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "abc"
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="must be a valid decimal value"):
        load_config(path)


def test_load_config_rejects_boolean_rate(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: true
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="must be a decimal value greater than 1"):
        load_config(path)


def test_load_config_rejects_missing_divide_to_base(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
""",
    )
    with pytest.raises(ConfigError, match="divide_to_base"):
        load_config(path)


def test_load_config_parses_rate_with_numeric_yaml_value(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: 7.8
    divide_to_base: true
""",
    )
    config = load_config(path)
    rule = config.plan.fx_rates["HKD"]
    assert rule.rate == Decimal("7.8")
    assert rule.rate_text == "7.8"


def test_load_config_parses_absolute_api_key_path(tmp_path: Path) -> None:
    absolute_key = tmp_path / "absolute_key"
    absolute_key.write_text("x\n", encoding="utf-8")
    path = _write_config(
        tmp_path,
        f"""version: 1
secrets:
  api_key_file: {absolute_key}
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
    )
    config = load_config(path)
    assert config.secrets.api_key_file == absolute_key


def test_parse_rate_rounds_to_three_decimal_places(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.80123"
    divide_to_base: true
""",
    )
    config = load_config(path)
    assert config.plan.fx_rates["HKD"].rate_text == "7.801"


def test_write_config_template_refuses_existing_file_without_force(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("existing", encoding="utf-8")

    with pytest.raises(ConfigError, match="already exists"):
        write_config_template(path)


def test_write_config_template_overwrites_with_force(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("existing", encoding="utf-8")

    write_config_template(path, force=True)

    assert path.read_text(encoding="utf-8").startswith("version: 1")


def test_write_config_template_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "config.yaml"

    write_config_template(path)

    assert path.read_text(encoding="utf-8").startswith("version: 1")


def test_load_config_rejects_non_string_plan_name(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """version: 1
plan:
  alias: personal
  name: 123
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
    )
    with pytest.raises(ConfigError, match="plan.name must be a non-empty string"):
        load_config(path)


def test_load_config_rejects_list_rate(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        _VALID_CONFIG_HEAD
        + """  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate:
      - 7.8
    divide_to_base: true
""",
    )
    with pytest.raises(ConfigError, match="must be a decimal value greater than 1"):
        load_config(path)
