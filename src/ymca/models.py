from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

CONFIG_VERSION = 1
STATE_VERSION = 1

ClearedStatus = Literal["cleared", "uncleared", "reconciled"]


@dataclass(frozen=True, slots=True)
class FxRule:
    rate: Decimal
    rate_text: str
    divide_to_base: bool

    def pair_label(self, *, base_currency: str, source_currency: str) -> str:
        if self.divide_to_base:
            return f"{source_currency}/{base_currency}"
        return f"{base_currency}/{source_currency}"

    @property
    def stronger_currency_is_base(self) -> bool:
        return self.divide_to_base


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
class PlanState:
    plan_id: str | None
    account_ids: Mapping[str, str]
    server_knowledge: int | None
    sentinel_ids: Mapping[str, str] = field(default_factory=dict)
    """Tracked-balance sentinel transaction ids keyed by account alias.

    Persisting these lets ``ymca sync`` look the sentinel up directly with
    ``get_transaction_detail`` on every run, which is necessary because the
    delta sync only returns transactions that changed since the last saved
    ``server_knowledge`` -- and the sentinel only changes when WE update it.
    """


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
    cleared_balance_milliunits: int = 0


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
    payee_id: str | None = None
    payee_name: str | None = None
    cleared: ClearedStatus = "uncleared"
    paired_transfer_counted: bool | None = None


@dataclass(frozen=True, slots=True)
class RemoteSubTransaction:
    amount_milliunits: int
    payee_id: str | None = None
    payee_name: str | None = None
    category_id: str | None = None
    memo: str | None = None


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
class NewTransactionRequest:
    account_id: str
    date: date
    amount_milliunits: int
    memo: str
    payee_name: str | None
    cleared: ClearedStatus
    flag_color: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedBindings:
    plan: PlanConfig
    plan_id: str
    account_ids: Mapping[str, str]
    remote_accounts_by_alias: Mapping[str, RemoteAccount] = field(default_factory=dict)


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
class SentinelSnapshot:
    """Parsed state of an existing sentinel transaction for a tracked account."""

    id: str
    date: date
    memo: str
    cleared: ClearedStatus
    deleted: bool
    balance_milliunits: int


@dataclass(frozen=True, slots=True)
class BalanceContribution:
    """A signed local-currency contribution from one transaction to the running balance."""

    transaction_id: str
    signed_source_milliunits: int
    reason: str


@dataclass(frozen=True, slots=True)
class AmbiguousTransfer:
    """A zero-amount transfer whose direction could not be inferred."""

    transaction_id: str
    date: date
    account_alias: str
    memo_amount_milliunits: int
    currency: str


@dataclass(frozen=True, slots=True)
class PreparedTrackingUpdate:
    """Local-currency tracking plan for a single account in a single run."""

    account_alias: str
    currency: str
    account_id: str
    account_name: str
    prior_sentinel: SentinelSnapshot | None
    prior_balance_milliunits: int
    contributions: tuple[BalanceContribution, ...]
    ambiguous_transfers: tuple[AmbiguousTransfer, ...]
    new_balance_milliunits: int
    ynab_cleared_balance_milliunits: int
    stronger_currency: str
    drift_milliunits_stronger: int
    within_tolerance: bool
    rebuild: bool
    create_sentinel: NewTransactionRequest | None
    update_sentinel: TransactionUpdateRequest | None
    memo_flips: tuple[TransactionUpdateRequest, ...] = ()
    """Pending ``[FX]`` \u2194 ``[FX+]`` marker rewrites (and legacy \u2192 current
    migrations) for transactions whose counted state changed this run. Applied
    before the sentinel upsert via a batched ``update_transactions`` call."""


@dataclass(frozen=True, slots=True)
class PreparedConversion:
    bindings: ResolvedBindings
    sync_request: SyncRequest
    queried_account_ids: tuple[str, ...]
    fetched_transactions: int
    fetched_server_knowledge: int
    updates: tuple[PreparedUpdate, ...]
    skipped: tuple[SkippedTransaction, ...]
    tracking: tuple[PreparedTrackingUpdate, ...] = ()
    rebuild_balance: bool = False


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    prepared: PreparedConversion
    applied: bool
    writes_performed: int
    saved_server_knowledge: int | None
    new_state: AppState
    sentinel_writes: int = 0
    sentinels_created: int = 0


@dataclass(frozen=True, slots=True)
class TrackedBalance:
    """Human-facing summary of a tracked balance as of a point in time."""

    account_alias: str
    currency: str
    balance_milliunits: int
    as_of: datetime
