from __future__ import annotations

import getpass
import os
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import UUID

import yaml
import ynab
from ynab.rest import ApiException  # type: ignore[attr-defined]

CONFIG_VERSION = 1
API_KEY_ENV_VAR = "YNAB_API_KEY"
CONFIG_PATH_ENV_VAR = "YMCA_CONFIG_PATH"

_THOUSAND = Decimal("1000")
_TWO_PLACES = Decimal("0.01")
_THREE_PLACES = Decimal("0.001")
_WHOLE_MILLIUNIT = Decimal("1")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_AMOUNT_SIGN_PATTERN = r"(?:[+-]/[+-]|[+-])?"
_AMOUNT_NUMBER_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_AMOUNT_PATTERN = rf"{_AMOUNT_SIGN_PATTERN}{_AMOUNT_NUMBER_PATTERN}"
_SEPARATOR_PATTERN = r"(?:\||·)"
_NORMALIZE_AMOUNT_RE = re.compile(
    rf"(?P<sign>{_AMOUNT_SIGN_PATTERN})(?P<number>{_AMOUNT_NUMBER_PATTERN})"
)

FX_MARKER_RE = re.compile(
    r"\[FX(?P<counted>\+)?\]\s+"
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\s+(?P<pair>[A-Z]{3}/[A-Z]{3})\)"
)
LEGACY_FX_MARKER_RE = re.compile(
    rf"(?P<amount>{_AMOUNT_PATTERN})\s+"
    r"(?P<currency>[A-Z]{3})\s+"
    r"\(FX rate:\s*(?P<rate>[0-9]+(?:\.[0-9]+)?)\)"
)


class YmcaError(Exception):
    """Base exception for the deprecated one-off scripts."""


class ConfigError(YmcaError):
    """Raised when the script config is missing or invalid."""


class SecretError(YmcaError):
    """Raised when the YNAB API key cannot be loaded."""


class ApiError(YmcaError):
    """Raised when YNAB API interaction fails."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UserInputError(YmcaError):
    """Raised when CLI input is invalid or incomplete."""


ClearedStatus = Literal["cleared", "reconciled", "uncleared"]


@dataclass(frozen=True, slots=True)
class FxRule:
    rate: Decimal
    rate_text: str
    divide_to_base: bool


@dataclass(frozen=True, slots=True)
class AccountConfig:
    alias: str
    name: str
    currency: str
    enabled: bool
    track_local_balance: bool = False


@dataclass(frozen=True, slots=True)
class PlanConfig:
    alias: str
    name: str
    base_currency: str
    accounts: tuple[AccountConfig, ...]
    fx_rates: Mapping[str, FxRule]


@dataclass(frozen=True, slots=True)
class SecretsConfig:
    api_key_file: Path | None


@dataclass(frozen=True, slots=True)
class AppConfig:
    version: int
    plan: PlanConfig
    secrets: SecretsConfig


@dataclass(frozen=True, slots=True)
class RemotePlan:
    id: str
    name: str


@dataclass(frozen=True, slots=True)
class RemoteAccount:
    id: str
    name: str
    deleted: bool


@dataclass(frozen=True, slots=True)
class AccountSnapshot:
    accounts: tuple[RemoteAccount, ...]
    server_knowledge: int


@dataclass(frozen=True, slots=True)
class RemoteTransaction:
    id: str
    date: date
    amount_milliunits: int
    memo: str | None
    account_id: str
    transfer_account_id: str | None
    transfer_transaction_id: str | None
    deleted: bool


@dataclass(frozen=True, slots=True)
class RemoteTransactionDetail:
    id: str
    date: date
    amount_milliunits: int
    memo: str | None
    account_id: str
    transfer_account_id: str | None
    transfer_transaction_id: str | None
    deleted: bool
    subtransaction_count: int
    payee_id: str | None = None
    payee_name: str | None = None
    category_id: str | None = None
    cleared: ClearedStatus = "uncleared"
    approved: bool = False
    flag_color: str | None = None
    subtransactions: tuple[RemoteSubTransaction, ...] = ()


@dataclass(frozen=True, slots=True)
class RemoteSubTransaction:
    amount_milliunits: int
    payee_id: str | None = None
    payee_name: str | None = None
    category_id: str | None = None
    memo: str | None = None


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    transactions: tuple[RemoteTransaction, ...]
    server_knowledge: int


@dataclass(frozen=True, slots=True)
class TransactionUpdateRequest:
    transaction_id: str
    amount_milliunits: int | None
    memo: str
    flag_color: str | None = None
    account_id: str | None = None
    date: date | None = None
    payee_id: str | None = None
    payee_name: str | None = None
    category_id: str | None = None
    cleared: ClearedStatus | None = None
    approved: bool | None = None
    subtransactions: tuple[RemoteSubTransaction, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedBindings:
    plan: PlanConfig
    plan_id: str
    account_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SkippedTransaction:
    transaction_id: str
    date: date
    account_alias: str | None
    reason: str


class YnabGateway(Protocol):
    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]: ...

    def list_accounts(self, plan_id: str) -> AccountSnapshot: ...

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot: ...

    def get_transaction_detail(
        self, plan_id: str, transaction_id: str
    ) -> RemoteTransactionDetail: ...

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None: ...

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None: ...


def default_config_path() -> Path:
    configured = os.getenv(CONFIG_PATH_ENV_VAR)
    if configured:
        return Path(configured).expanduser()

    xdg_config_home = os.getenv("XDG_CONFIG_HOME")
    base = Path(xdg_config_home).expanduser() if xdg_config_home else Path.home() / ".config"
    return base / "ymca" / "config.yaml"


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

    plan = PlanConfig(
        alias=alias,
        name=name,
        base_currency=base_currency,
        accounts=tuple(accounts),
        fx_rates=fx_rates,
    )
    return AppConfig(version=version, plan=plan, secrets=secrets)


def resolve_bindings(plan: PlanConfig, gateway: YnabGateway) -> ResolvedBindings:
    remote_plans = gateway.list_plans(include_accounts=False)
    matching_plans = [remote_plan for remote_plan in remote_plans if remote_plan.name == plan.name]
    if not matching_plans:
        raise ApiError(f"Configured plan {plan.name!r} was not found in YNAB.")
    if len(matching_plans) > 1:
        raise ApiError(f"Configured plan {plan.name!r} matched multiple YNAB plans.")

    remote_plan = matching_plans[0]
    account_snapshot = gateway.list_accounts(remote_plan.id)
    accounts_by_name: dict[str, list[RemoteAccount]] = defaultdict(list)
    for remote_account in account_snapshot.accounts:
        if remote_account.deleted:
            continue
        accounts_by_name[remote_account.name].append(remote_account)

    account_ids: dict[str, str] = {}
    for account in plan.accounts:
        matches = accounts_by_name.get(account.name, [])
        if not matches:
            raise ApiError(
                "Configured account "
                f"{account.alias!r} with name {account.name!r} was not found in YNAB."
            )
        if len(matches) > 1:
            raise ApiError(
                "Configured account "
                f"{account.alias!r} with name {account.name!r} matched multiple YNAB accounts."
            )
        account_ids[account.alias] = matches[0].id

    return ResolvedBindings(
        plan=plan,
        plan_id=remote_plan.id,
        account_ids=account_ids,
    )


def has_fx_marker(memo: str | None) -> bool:
    if memo is None:
        return False
    return FX_MARKER_RE.search(memo) is not None


def has_legacy_fx_marker(memo: str | None) -> bool:
    if memo is None:
        return False
    return LEGACY_FX_MARKER_RE.search(memo) is not None


def replace_legacy_fx_marker(
    memo: str,
    *,
    pair_label_for_currency: dict[str, str],
    transfer: bool,
) -> str | None:
    del transfer

    match = LEGACY_FX_MARKER_RE.search(memo)
    if match is None:
        return None

    currency = match.group("currency")
    pair_label = pair_label_for_currency.get(currency)
    if pair_label is None:
        return None

    marker = _build_fx_marker_from_amount_text(
        amount_text=match.group("amount"),
        source_currency=currency,
        rate_text=match.group("rate"),
        pair_label=pair_label,
    )
    before = _trim_separator_suffix(memo[: match.start()])
    after = _trim_separator_prefix(memo[match.end() :])
    parts = [part for part in (before, after, marker) if part]
    rewritten = " | ".join(parts)
    if rewritten == memo:
        return None
    return rewritten


def amount_text_to_milliunits(
    amount_text: str,
    *,
    fallback_sign: int | None = None,
) -> int:
    match = _NORMALIZE_AMOUNT_RE.fullmatch(amount_text)
    if match is None:
        raise ValueError(f"Unsupported amount text: {amount_text!r}")

    sign_token = match.group("sign") or ""
    number = match.group("number")
    sign = _resolve_amount_sign(sign_token, fallback_sign=fallback_sign)
    magnitude = (Decimal(number.replace(",", "")) * _THOUSAND).quantize(
        _WHOLE_MILLIUNIT,
        rounding=ROUND_HALF_UP,
    )
    return sign * int(magnitude)


def format_milliunits(
    amount_milliunits: int,
    *,
    places: int,
    always_show_sign: bool = False,
) -> str:
    if places not in {2, 3}:
        raise ValueError("places must be 2 or 3")

    quantum = _TWO_PLACES if places == 2 else _THREE_PLACES
    amount = (Decimal(amount_milliunits) / _THOUSAND).quantize(quantum, rounding=ROUND_HALF_UP)
    if amount == 0:
        amount = abs(amount)
    sign = "+" if always_show_sign else ""
    return f"{amount:{sign},.{places}f}"


class YnabClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._api_client_context: Any | None = None
        self._plans_api: Any | None = None
        self._accounts_api: Any | None = None
        self._transactions_api: Any | None = None

    def __enter__(self) -> YnabClient:
        configuration = ynab.Configuration(access_token=self._api_key)
        self._api_client_context = ynab.ApiClient(configuration)
        api_client = self._api_client_context.__enter__()  # type: ignore[no-untyped-call]
        self._plans_api = ynab.PlansApi(api_client)
        self._accounts_api = ynab.AccountsApi(api_client)
        self._transactions_api = ynab.TransactionsApi(api_client)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        if self._api_client_context is None:
            return False
        return bool(self._api_client_context.__exit__(exc_type, exc, traceback))

    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]:
        plans_api = _require_api(self._plans_api, "PlansApi")
        try:
            response = plans_api.get_plans(include_accounts=include_accounts)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("list plans", exc),
                status=_api_exception_status(exc),
            ) from exc

        return tuple(
            RemotePlan(id=str(plan.id), name=str(plan.name))
            for plan in response.data.plans
        )

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        accounts_api = _require_api(self._accounts_api, "AccountsApi")
        try:
            response = accounts_api.get_accounts(plan_id)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("list accounts", exc),
                status=_api_exception_status(exc),
            ) from exc

        accounts = tuple(
            RemoteAccount(
                id=str(account.id),
                name=str(account.name),
                deleted=bool(account.deleted),
            )
            for account in response.data.accounts
        )
        return AccountSnapshot(
            accounts=accounts,
            server_knowledge=int(response.data.server_knowledge),
        )

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot:
        transactions_api = _require_api(self._transactions_api, "TransactionsApi")
        try:
            response = transactions_api.get_transactions_by_account(
                plan_id,
                account_id,
                since_date=since_date,
                last_knowledge_of_server=last_knowledge_of_server,
            )
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("list account transactions", exc),
                status=_api_exception_status(exc),
            ) from exc

        return TransactionSnapshot(
            transactions=tuple(
                _map_transaction(transaction) for transaction in response.data.transactions
            ),
            server_knowledge=int(response.data.server_knowledge),
        )

    def get_transaction_detail(self, plan_id: str, transaction_id: str) -> RemoteTransactionDetail:
        transactions_api = _require_api(self._transactions_api, "TransactionsApi")
        try:
            response = transactions_api.get_transaction_by_id(plan_id, transaction_id)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("get transaction detail", exc),
                status=_api_exception_status(exc),
            ) from exc

        return _map_transaction_detail(response.data.transaction)

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        transactions_api = _require_api(self._transactions_api, "TransactionsApi")
        existing_kwargs: dict[str, Any] = {
            "amount": request.amount_milliunits,
            "memo": request.memo,
        }
        if request.account_id is not None:
            existing_kwargs["account_id"] = UUID(request.account_id)
        if request.date is not None:
            existing_kwargs["date"] = request.date
        if request.payee_id is not None:
            existing_kwargs["payee_id"] = UUID(request.payee_id)
        if request.payee_name is not None:
            existing_kwargs["payee_name"] = request.payee_name
        if request.category_id is not None:
            existing_kwargs["category_id"] = UUID(request.category_id)
        if request.cleared is not None:
            existing_kwargs["cleared"] = ynab.TransactionClearedStatus(request.cleared)
        if request.approved is not None:
            existing_kwargs["approved"] = request.approved
        if request.flag_color is not None:
            existing_kwargs["flag_color"] = ynab.TransactionFlagColor(request.flag_color)
        if request.subtransactions:
            existing_kwargs["subtransactions"] = [
                _build_save_subtransaction(subtransaction)
                for subtransaction in request.subtransactions
            ]
        payload = ynab.PutTransactionWrapper(
            transaction=ynab.ExistingTransaction(**existing_kwargs)
        )
        try:
            transactions_api.update_transaction(plan_id, request.transaction_id, payload)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("update transaction", exc),
                status=_api_exception_status(exc),
            ) from exc

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None:
        if not requests:
            return

        transactions_api = _require_api(self._transactions_api, "TransactionsApi")
        payload = ynab.PatchTransactionsWrapper(
            transactions=[
                ynab.SaveTransactionWithIdOrImportId(
                    id=request.transaction_id,
                    amount=request.amount_milliunits,
                    memo=request.memo,
                )
                for request in requests
            ]
        )
        try:
            transactions_api.update_transactions(plan_id, payload)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("update transactions", exc),
                status=_api_exception_status(exc),
            ) from exc


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


def _build_fx_marker_from_amount_text(
    *,
    amount_text: str,
    source_currency: str,
    rate_text: str,
    pair_label: str,
) -> str:
    normalized_amount = _normalize_amount_text(amount_text)
    return f"[FX] {normalized_amount} {source_currency} (rate: {rate_text} {pair_label})"


def _trim_separator_suffix(text: str) -> str:
    return re.sub(rf"(?:\s*{_SEPARATOR_PATTERN}\s*)+$", "", text).strip()


def _trim_separator_prefix(text: str) -> str:
    return re.sub(rf"^(?:\s*{_SEPARATOR_PATTERN}\s*)+", "", text).strip()


def _normalize_amount_text(amount_text: str) -> str:
    match = _NORMALIZE_AMOUNT_RE.fullmatch(amount_text)
    if match is None:
        return amount_text

    sign = match.group("sign") or ""
    number = match.group("number")
    integer_part, dot, fractional_part = number.partition(".")
    grouped_integer = f"{int(integer_part.replace(',', '')):,}"
    if not dot:
        return f"{sign}{grouped_integer}"
    return f"{sign}{grouped_integer}.{fractional_part}"


def _resolve_amount_sign(sign_token: str, *, fallback_sign: int | None) -> int:
    if sign_token == "-":
        return -1
    if sign_token in {"", "+"}:
        return 1
    if fallback_sign is not None and fallback_sign != 0:
        return -1 if fallback_sign < 0 else 1
    return -1 if sign_token.startswith("-") else 1


def _format_api_exception(action: str, exc: ApiException) -> str:
    status = _api_exception_status(exc)
    if status is None:
        return f"Failed to {action} via YNAB API."
    return f"Failed to {action} via YNAB API. status={status}"


def _api_exception_status(exc: ApiException) -> int | None:
    raw_status = getattr(exc, "status", None)
    if isinstance(raw_status, int):
        return raw_status
    return None


def _require_api(api: Any | None, api_name: str) -> Any:
    if api is None:
        raise RuntimeError(f"{api_name} is only available inside an active YnabClient context.")
    return api


def _map_transaction(raw_transaction: Any) -> RemoteTransaction:
    return RemoteTransaction(
        id=str(raw_transaction.id),
        date=_require_date(raw_transaction.var_date, "transaction.var_date"),
        amount_milliunits=int(raw_transaction.amount),
        memo=raw_transaction.memo,
        account_id=str(raw_transaction.account_id),
        transfer_account_id=_optional_string(raw_transaction.transfer_account_id),
        transfer_transaction_id=_optional_string(raw_transaction.transfer_transaction_id),
        deleted=bool(raw_transaction.deleted),
    )


def _map_transaction_detail(raw_transaction: Any) -> RemoteTransactionDetail:
    raw_subtransactions = getattr(raw_transaction, "subtransactions", None) or []
    return RemoteTransactionDetail(
        id=str(raw_transaction.id),
        date=_require_date(raw_transaction.var_date, "transaction.var_date"),
        amount_milliunits=int(raw_transaction.amount),
        memo=raw_transaction.memo,
        account_id=str(raw_transaction.account_id),
        transfer_account_id=_optional_string(raw_transaction.transfer_account_id),
        transfer_transaction_id=_optional_string(raw_transaction.transfer_transaction_id),
        deleted=bool(raw_transaction.deleted),
        subtransaction_count=len(raw_subtransactions),
        payee_id=_optional_string(getattr(raw_transaction, "payee_id", None)),
        payee_name=_optional_string(getattr(raw_transaction, "payee_name", None)),
        category_id=_optional_string(getattr(raw_transaction, "category_id", None)),
        cleared=_map_cleared(getattr(raw_transaction, "cleared", None)),
        approved=bool(getattr(raw_transaction, "approved", False)),
        flag_color=_map_flag_color(getattr(raw_transaction, "flag_color", None)),
        subtransactions=tuple(
            _map_subtransaction(subtransaction) for subtransaction in raw_subtransactions
        ),
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _build_save_subtransaction(subtransaction: RemoteSubTransaction) -> Any:
    kwargs: dict[str, Any] = {
        "amount": subtransaction.amount_milliunits,
        "memo": subtransaction.memo,
        "payee_name": subtransaction.payee_name,
    }
    if subtransaction.payee_id is not None:
        kwargs["payee_id"] = UUID(subtransaction.payee_id)
    if subtransaction.category_id is not None:
        kwargs["category_id"] = UUID(subtransaction.category_id)
    return ynab.SaveSubTransaction(**kwargs)


def _map_subtransaction(raw_subtransaction: Any) -> RemoteSubTransaction:
    return RemoteSubTransaction(
        amount_milliunits=int(raw_subtransaction.amount),
        payee_id=_optional_string(getattr(raw_subtransaction, "payee_id", None)),
        payee_name=_optional_string(getattr(raw_subtransaction, "payee_name", None)),
        category_id=_optional_string(getattr(raw_subtransaction, "category_id", None)),
        memo=getattr(raw_subtransaction, "memo", None),
    )


def _map_cleared(value: Any) -> ClearedStatus:
    if value is None:
        return "uncleared"
    text = str(value).lower()
    if "reconciled" in text:
        return "reconciled"
    if "cleared" in text:
        return "cleared"
    return "uncleared"


def _map_flag_color(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).lower()
    allowed = {
        "red",
        "orange",
        "yellow",
        "green",
        "blue",
        "purple",
    }
    return text if text in allowed else None


def _require_date(value: object, field_name: str) -> date:
    if isinstance(value, date):
        return value
    raise ApiError(f"Unexpected YNAB value for {field_name}: expected date, got {value!r}.")
