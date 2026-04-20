from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import yaml

from .errors import ConfigError
from .models import (
    CONFIG_VERSION,
    AccountConfig,
    AppConfig,
    FxRule,
    PlanConfig,
    SecretsConfig,
)

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def write_config_template(path: Path, *, force: bool = False) -> None:
    if path.exists() and not force:
        raise ConfigError(f"Config file already exists at {path}. Use --force to overwrite it.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_template(), encoding="utf-8")


def load_config(path: Path) -> AppConfig:
    raw = _load_yaml_mapping(path, "config")
    version = _parse_int(raw.get("version", CONFIG_VERSION), "version")
    if version != CONFIG_VERSION:
        raise ConfigError(f"Unsupported config version {version}. Expected {CONFIG_VERSION}.")

    secrets_data = raw.get("secrets", {})
    secrets_map = _require_mapping(secrets_data, "secrets")
    secrets = SecretsConfig(
        api_key_file=_parse_optional_path(
            secrets_map.get("api_key_file"),
            "secrets.api_key_file",
            base_dir=path.parent,
        )
    )

    plan_data = _require_mapping(raw.get("plan"), "plan")
    alias = _parse_non_empty_string(plan_data.get("alias"), "plan.alias")
    name = _parse_non_empty_string(plan_data.get("name"), "plan.name")
    base_currency = _parse_currency(plan_data.get("base_currency"), "plan.base_currency")

    accounts_data = _require_mapping(raw.get("accounts"), "accounts")
    accounts: list[AccountConfig] = []
    account_names: set[str] = set()
    for alias_key, raw_account in accounts_data.items():
        alias_text = _parse_non_empty_string(alias_key, "accounts.<alias>")
        account_map = _require_mapping(raw_account, f"accounts.{alias_text}")
        account_name = _parse_non_empty_string(
            account_map.get("name"),
            f"accounts.{alias_text}.name",
        )
        if account_name in account_names:
            raise ConfigError(f"Duplicate configured account name {account_name!r}.")
        account_names.add(account_name)
        accounts.append(
            AccountConfig(
                alias=alias_text,
                name=account_name,
                currency=_parse_currency(
                    account_map.get("currency"),
                    f"accounts.{alias_text}.currency",
                ),
                enabled=_parse_bool(
                    account_map.get("enabled", True),
                    f"accounts.{alias_text}.enabled",
                ),
                track_local_balance=_parse_bool(
                    account_map.get("track_local_balance", False),
                    f"accounts.{alias_text}.track_local_balance",
                ),
            )
        )

    if not accounts:
        raise ConfigError("At least one account must be configured.")

    fx_rates_data = _require_mapping(raw.get("fx_rates"), "fx_rates")
    fx_rates: dict[str, FxRule] = {}
    for currency_key, raw_rule in fx_rates_data.items():
        source_currency = _parse_currency(currency_key, "fx_rates.<currency>")
        rule_map = _require_mapping(raw_rule, f"fx_rates.{source_currency}")
        rate, rate_text = _parse_rate(rule_map.get("rate"), f"fx_rates.{source_currency}.rate")
        fx_rates[source_currency] = FxRule(
            rate=rate,
            rate_text=rate_text,
            divide_to_base=_parse_bool(
                rule_map.get("divide_to_base"),
                f"fx_rates.{source_currency}.divide_to_base",
            ),
        )

    enabled_accounts = [account for account in accounts if account.enabled]
    if not enabled_accounts:
        raise ConfigError("At least one account must be enabled.")

    for account in enabled_accounts:
        if account.currency == base_currency:
            raise ConfigError(
                f"Enabled account {account.alias!r} uses the base currency {base_currency}; "
                "v1 only supports foreign-currency accounts."
            )
        if account.currency not in fx_rates:
            raise ConfigError(
                "No fx_rates entry found for enabled account "
                f"{account.alias!r} currency {account.currency}."
            )

    for account in accounts:
        if account.track_local_balance and account.currency == base_currency:
            raise ConfigError(
                f"Account {account.alias!r} uses the base currency {base_currency}; "
                "track_local_balance is only valid for foreign-currency accounts."
            )

    plan = PlanConfig(
        alias=alias,
        name=name,
        base_currency=base_currency,
        accounts=tuple(accounts),
        fx_rates=fx_rates,
    )
    return AppConfig(version=version, plan=plan, secrets=secrets)


def _render_template() -> str:
    return """version: 1
secrets:
  api_key_file: ~/.config/ymca/ynab_api_key

plan:
  alias: personal
  name: Example YNAB Plan Name
  base_currency: USD

accounts:
  hkd_wallet:
    name: Example HKD Account
    currency: HKD
    enabled: true
    # Optional: opt-in to local-currency balance tracking. When true, ymca sync
    # will maintain a sentinel transaction in this account whose memo shows the
    # running source-currency balance. Only valid for foreign-currency accounts.
    # track_local_balance: false
  gbp_wallet:
    name: Example GBP Account
    currency: GBP
    enabled: true

fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
  GBP:
    rate: "1.35"
    divide_to_base: false
"""


def _load_yaml_mapping(path: Path, label: str) -> Mapping[str, object]:
    if not path.is_file():
        raise ConfigError(f"{label.capitalize()} file not found at {path}.")

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {label} YAML at {path}: {exc}") from exc

    return _require_mapping(loaded, label)


def _require_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{field_name} must be a mapping.")
    return value


def _parse_non_empty_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _parse_currency(value: object, field_name: str) -> str:
    text = _parse_non_empty_string(value, field_name).upper()
    if not _CURRENCY_RE.fullmatch(text):
        raise ConfigError(f"{field_name} must be a 3-letter currency code.")
    return text


def _parse_bool(value: object, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{field_name} must be true or false.")
    return value


def _parse_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"{field_name} must be an integer.")
    return value


def _parse_rate(value: object, field_name: str) -> tuple[Decimal, str]:
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a decimal value greater than 1.")
    if not isinstance(value, (int, float, str)):
        raise ConfigError(f"{field_name} must be a decimal value greater than 1.")

    raw_text = str(value).strip()
    try:
        rate = Decimal(raw_text)
    except (InvalidOperation, ValueError) as exc:
        raise ConfigError(f"{field_name} must be a valid decimal value.") from exc

    if rate <= Decimal("1"):
        raise ConfigError(f"{field_name} must be greater than 1.")

    rounded = rate.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    normalized = format(rounded.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")
    return rate, normalized


def _parse_optional_path(
    value: object,
    field_name: str,
    *,
    base_dir: Path,
) -> Path | None:
    if value is None:
        return None

    raw_path = _parse_non_empty_string(value, field_name)
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path
