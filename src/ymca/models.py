from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

CONFIG_VERSION = 1
STATE_VERSION = 1


@dataclass(frozen=True, slots=True)
class FxRule:
    rate: Decimal
    rate_text: str
    divide_to_base: bool

    def pair_label(self, *, base_currency: str, source_currency: str) -> str:
        if self.divide_to_base:
            return f"{source_currency}/{base_currency}"
        return f"{base_currency}/{source_currency}"


@dataclass(frozen=True, slots=True)
class AccountConfig:
    alias: str
    name: str
    currency: str
    enabled: bool


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
class PlanState:
    plan_id: str | None
    account_ids: Mapping[str, str]
    server_knowledge: int | None


@dataclass(frozen=True, slots=True)
class AppState:
    version: int
    plans: Mapping[str, PlanState]


@dataclass(frozen=True, slots=True)
class RemoteAccount:
    id: str
    name: str
    deleted: bool
    closed: bool = False


@dataclass(frozen=True, slots=True)
class RemotePlan:
    id: str
    name: str
    accounts: tuple[RemoteAccount, ...] = ()


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


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    transactions: tuple[RemoteTransaction, ...]
    server_knowledge: int


@dataclass(frozen=True, slots=True)
class TransactionUpdateRequest:
    transaction_id: str
    amount_milliunits: int | None
    memo: str


@dataclass(frozen=True, slots=True)
class ResolvedBindings:
    plan: PlanConfig
    plan_id: str
    account_ids: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SyncRequest:
    last_knowledge_of_server: int | None
    since_date: date | None
    used_bootstrap: bool


@dataclass(frozen=True, slots=True)
class PreparedUpdate:
    transaction_id: str
    date: date
    account_alias: str
    account_name: str
    is_transfer: bool
    source_currency: str
    source_amount_milliunits: int
    converted_currency: str
    converted_amount_milliunits: int
    rate_text: str
    pair_label: str
    old_memo: str | None
    new_memo: str
    request: TransactionUpdateRequest


@dataclass(frozen=True, slots=True)
class SkippedTransaction:
    transaction_id: str
    date: date
    account_alias: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class PreparedConversion:
    bindings: ResolvedBindings
    sync_request: SyncRequest
    queried_account_ids: tuple[str, ...]
    fetched_transactions: int
    fetched_server_knowledge: int
    updates: tuple[PreparedUpdate, ...]
    skipped: tuple[SkippedTransaction, ...]


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    prepared: PreparedConversion
    applied: bool
    writes_performed: int
    saved_server_knowledge: int | None
    new_state: AppState
