"""Integration-test harness for YMCA.

This module provides the glue that makes integration tests safe and cheap:

* :class:`CountingYnabClient` wraps a real :class:`ymca.ynab_client.YnabClient`
  with per-call counting, a session-wide budget cap, YNAB 429 rate-limit
  backoff, and a plan-id guard that refuses writes outside the dedicated test
  plan. It also exposes test-only helpers for ``create_transaction`` /
  ``delete_transaction`` because the production adapter intentionally omits
  those.
* Helpers for creating account/transaction payloads and resolving the
  dedicated integration-plan accounts.

All live YNAB traffic during an integration session MUST flow through a
single :class:`CountingYnabClient`; any test that bypasses it invalidates the
rate-limit guarantees documented in ``docs/testing.md``.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Any, Protocol, TypeVar, cast

import ynab
from ynab.rest import ApiException  # type: ignore[attr-defined]

from ymca.errors import ApiError
from ymca.models import (
    AccountConfig,
    AccountSnapshot,
    AppState,
    FxRule,
    NewTransactionRequest,
    PlanConfig,
    RemoteAccount,
    RemotePlan,
    RemoteTransactionDetail,
    TransactionSnapshot,
    TransactionUpdateRequest,
)
from ymca.ynab_client import YnabClient

INTEGRATION_PLAN_NAME = "_Intergration Test_ USE ONLY"
"""Name of the YNAB plan the integration suite is allowed to touch.

Matches ``AGENTS.md`` verbatim (including the existing typo). Changing this
would lose protection against writes to the wrong plan.
"""

DEFAULT_SESSION_BUDGET = 150
"""Per-session API-call budget.

Set well below the YNAB 200 req/hour cap to keep headroom for retries, other
tooling the developer may run in parallel, and at least one immediate re-run
on a failure.
"""

DEFAULT_CLEANUP_SWEEPS = 3
DEFAULT_CLEANUP_RETRY_ATTEMPTS = 3
DEFAULT_CLEANUP_RETRY_BACKOFF_SECONDS = 0.5

_TRANSIENT_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_STATUS_CODE_PATTERN = re.compile(r"\bstatus=(\d{3})\b")

_T = TypeVar("_T")


class BudgetExceededError(RuntimeError):
    """Raised when the integration session exceeds :data:`DEFAULT_SESSION_BUDGET`.

    Aborting loudly is safer than silently exhausting the YNAB API key for the
    next hour, which would break subsequent local runs and any other tooling
    that shares the key.
    """


class UnsafeWriteError(RuntimeError):
    """Raised when a test attempts to mutate a plan other than the test plan.

    This is defense in depth: even if an ``--apply`` run somehow resolves the
    wrong plan, the CountingYnabClient refuses to forward the write.
    """


class IntegrationCleanupError(RuntimeError):
    """Raised when the integration harness cannot verify the test plan is clean."""


@dataclass
class _CallCounter:
    budget: int
    count: int = 0

    def tick(self) -> int:
        self.count += 1
        if self.count > self.budget:
            raise BudgetExceededError(
                f"Integration session exceeded {self.budget} API calls. "
                "If this is expected, raise DEFAULT_SESSION_BUDGET in "
                "tests/integration/helpers.py and update docs/testing.md."
            )
        return self.count


class CountingYnabClient:
    """YnabGateway that counts SDK calls and refuses unsafe writes.

    Wraps a :class:`YnabClient` for the standard gateway surface. It also
    owns its own :class:`ynab.TransactionsApi` instance for test-only
    create/delete helpers since the production adapter does not expose those.
    """

    def __init__(
        self,
        ynab_client: YnabClient,
        api_client: Any,
        *,
        budget: int = DEFAULT_SESSION_BUDGET,
    ) -> None:
        self._ynab = ynab_client
        self._accounts_api = ynab.AccountsApi(api_client)
        self._transactions_api = ynab.TransactionsApi(api_client)
        self._payees_api = ynab.PayeesApi(api_client)
        self._counter = _CallCounter(budget=budget)
        self._allowed_write_plan_id: str | None = None

    def set_allowed_write_plan_id(self, plan_id: str) -> None:
        """Record the single plan_id that this session is permitted to write to."""
        self._allowed_write_plan_id = plan_id

    @property
    def call_count(self) -> int:
        return self._counter.count

    @property
    def budget(self) -> int:
        return self._counter.budget

    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]:
        return self._invoke(
            lambda: self._ynab.list_plans(include_accounts=include_accounts)
        )

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        return self._invoke(lambda: self._ynab.list_accounts(plan_id))

    def create_account(
        self,
        plan_id: str,
        *,
        name: str,
        account_type: Any = ynab.AccountType.CHECKING,
        balance_milliunits: int = 0,
    ) -> RemoteAccount:
        """Create an on-budget account inside the integration test plan."""
        self._guard_write(plan_id)
        payload = ynab.PostAccountWrapper(
            account=ynab.SaveAccount(
                name=name,
                type=account_type,
                balance=balance_milliunits,
            )
        )
        response = self._invoke(lambda: self._accounts_api.create_account(plan_id, payload))
        return _map_remote_account(response.data.account)

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot:
        return self._invoke(
            lambda: self._ynab.list_transactions_by_account(
                plan_id,
                account_id,
                since_date=since_date,
                last_knowledge_of_server=last_knowledge_of_server,
            )
        )

    def get_transaction_detail(
        self, plan_id: str, transaction_id: str
    ) -> RemoteTransactionDetail:
        return self._invoke(
            lambda: self._ynab.get_transaction_detail(plan_id, transaction_id)
        )

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        self._guard_write(plan_id)
        self._invoke(lambda: self._ynab.update_transaction(plan_id, request))

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None:
        self._guard_write(plan_id)
        self._invoke(lambda: self._ynab.update_transactions(plan_id, requests))

    def create_transactions(
        self, plan_id: str, transactions: Sequence[Any]
    ) -> list[Any]:
        """Create transactions via the SDK; returns the created rows."""
        self._guard_write(plan_id)
        payload = ynab.PostTransactionsWrapper(transactions=list(transactions))
        response = self._invoke(
            lambda: self._transactions_api.create_transaction(plan_id, payload)
        )
        return list(getattr(response.data, "transactions", None) or [])

    def create_transaction(self, plan_id: str, request: NewTransactionRequest) -> str:
        """YnabGateway.create_transaction: forwards to the production adapter."""
        self._guard_write(plan_id)
        return self._invoke(lambda: self._ynab.create_transaction(plan_id, request))

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None:
        self._guard_write(plan_id)
        self._invoke(
            lambda: self._transactions_api.delete_transaction(plan_id, transaction_id)
        )

    def list_plan_transactions_raw(self, plan_id: str) -> list[Any]:
        """Return every :class:`ynab.TransactionDetail` in ``plan_id``.

        Used by the cleanup harness and the integration tests themselves. A
        single call replaces N per-account calls plus detail lookups.
        """
        response = self._invoke(lambda: self._transactions_api.get_transactions(plan_id))
        return list(response.data.transactions)

    def get_transfer_payee_id(self, plan_id: str, target_account_id: str) -> str | None:
        """Return the id of the transfer payee that mirrors ``target_account_id``."""
        response = self._invoke(lambda: self._payees_api.get_payees(plan_id))
        for payee in response.data.payees:
            transfer_account_id = getattr(payee, "transfer_account_id", None)
            if transfer_account_id and str(transfer_account_id) == target_account_id:
                return str(payee.id)
        return None

    def _guard_write(self, plan_id: str) -> None:
        if self._allowed_write_plan_id is None:
            raise UnsafeWriteError(
                "Write attempted before the integration harness resolved the "
                "test plan. This is a bug in the test harness."
            )
        if plan_id != self._allowed_write_plan_id:
            raise UnsafeWriteError(
                f"Refusing write to plan {plan_id!r}; only writes to "
                f"{self._allowed_write_plan_id!r} (the integration test plan) "
                "are permitted by the CountingYnabClient safety guard."
            )

    def _invoke(self, call: Callable[[], _T]) -> _T:
        self._counter.tick()
        try:
            return call()
        except (ApiException, ApiError) as exc:
            retry_after = _extract_retry_after_seconds(exc)
            if retry_after is None:
                raise
            time.sleep(retry_after)
            self._counter.tick()
            return call()


class IntegrationCleanupGateway(Protocol):
    def list_plan_transactions_raw(self, plan_id: str) -> list[Any]: ...

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None: ...


def clear_active_plan_transactions(
    gateway: IntegrationCleanupGateway,
    plan_id: str,
    *,
    max_sweeps: int = DEFAULT_CLEANUP_SWEEPS,
    retry_attempts: int = DEFAULT_CLEANUP_RETRY_ATTEMPTS,
    retry_backoff_seconds: float = DEFAULT_CLEANUP_RETRY_BACKOFF_SECONDS,
) -> None:
    """Delete every active transaction in ``plan_id`` and verify none remain."""
    last_failures: list[str] = []

    for _ in range(max_sweeps):
        active_transactions = _list_active_plan_transactions(
            gateway,
            plan_id,
            retry_attempts=retry_attempts,
            retry_backoff_seconds=retry_backoff_seconds,
        )
        if not active_transactions:
            return

        last_failures = []
        for transaction in active_transactions:
            transaction_id = str(transaction.id)
            try:
                _retry_cleanup_call(
                    partial(gateway.delete_transaction, plan_id, transaction_id),
                    retry_attempts=retry_attempts,
                    retry_backoff_seconds=retry_backoff_seconds,
                )
            except Exception as exc:
                last_failures.append(
                    f"{transaction_id}: {_summarize_cleanup_error(exc)}"
                )

    remaining_transactions = _list_active_plan_transactions(
        gateway,
        plan_id,
        retry_attempts=retry_attempts,
        retry_backoff_seconds=retry_backoff_seconds,
    )
    if not remaining_transactions:
        return

    remaining_ids = ", ".join(
        str(transaction.id) for transaction in remaining_transactions[:5]
    )
    extra_ids = len(remaining_transactions) - 5
    more_text = f" (+{extra_ids} more)" if extra_ids > 0 else ""
    failure_text = ""
    if last_failures:
        failure_text = f" Last failures: {'; '.join(last_failures[:3])}."
    raise IntegrationCleanupError(
        "Integration harness could not fully clear the dedicated test plan. "
        f"{len(remaining_transactions)} active transaction(s) remain "
        f"({remaining_ids}{more_text}).{failure_text}"
    )


def _list_active_plan_transactions(
    gateway: IntegrationCleanupGateway,
    plan_id: str,
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> list[Any]:
    try:
        transactions = cast(
            list[Any],
            _retry_cleanup_call(
                lambda: gateway.list_plan_transactions_raw(plan_id),
                retry_attempts=retry_attempts,
                retry_backoff_seconds=retry_backoff_seconds,
            ),
        )
    except Exception as exc:
        raise IntegrationCleanupError(
            "Integration harness could not list plan transactions during "
            f"cleanup: {_summarize_cleanup_error(exc)}"
        ) from exc
    return [
        transaction
        for transaction in transactions
        if not getattr(transaction, "deleted", False)
    ]


def _retry_cleanup_call(
    call: Callable[[], Any],
    *,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> Any:
    for attempt in range(retry_attempts):
        try:
            return call()
        except Exception as exc:
            is_last_attempt = attempt + 1 >= retry_attempts
            if is_last_attempt or not _is_retryable_cleanup_error(exc):
                raise
            time.sleep(_cleanup_retry_delay_seconds(exc, attempt, retry_backoff_seconds))

    raise AssertionError("cleanup retry loop exited unexpectedly")


def _cleanup_retry_delay_seconds(
    exc: BaseException, attempt: int, retry_backoff_seconds: float
) -> float:
    retry_after = _extract_retry_after_seconds(exc)
    if retry_after is not None:
        return float(retry_after)
    return float(retry_backoff_seconds * (2**attempt))


def _is_retryable_cleanup_error(exc: BaseException) -> bool:
    status_code = _status_code_from_exception(exc)
    return status_code in _TRANSIENT_RETRYABLE_STATUS_CODES


def _status_code_from_exception(exc: BaseException) -> int | None:
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        return status

    message = str(exc)
    match = _STATUS_CODE_PATTERN.search(message)
    if match is None:
        return None
    return int(match.group(1))


def _summarize_cleanup_error(exc: BaseException) -> str:
    status_code = _status_code_from_exception(exc)
    reason = getattr(exc, "reason", None)
    parts: list[str] = []
    if status_code is not None:
        parts.append(f"status={status_code}")
    if isinstance(reason, str) and reason.strip():
        parts.append(f"reason={reason.strip()}")
    if parts:
        return ", ".join(parts)

    first_line = next(
        (line.strip() for line in str(exc).splitlines() if line.strip()),
        type(exc).__name__,
    )
    return first_line


def _extract_retry_after_seconds(exc: BaseException) -> float | None:
    """Return ``Retry-After`` seconds iff this looks like a 429, else ``None``.

    Both :class:`ApiException` (raw SDK) and :class:`ApiError` (our wrapper)
    are accepted because ``CountingYnabClient`` may see either depending on
    how deep the failing call was.
    """
    status = getattr(exc, "status", None)
    message = str(exc)
    if status != 429 and "status=429" not in message:
        return None

    headers = getattr(exc, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        raw_retry_after = headers.get("Retry-After")
        if raw_retry_after is not None:
            try:
                return max(0.0, float(raw_retry_after))
            except (TypeError, ValueError):
                pass
    return 5.0


def build_new_transaction(
    *,
    account_id: str,
    date_: date,
    amount_milliunits: int,
    memo: str | None,
    payee_name: str | None = None,
    payee_id: str | None = None,
    subtransactions: Sequence[Any] = (),
    cleared: str = "uncleared",
) -> Any:
    """Construct a ``ynab.NewTransaction`` payload for ``create_transactions``.

    ``subtransactions`` must be a sequence of ``ynab.SaveSubTransaction``
    instances whose amounts sum to ``amount_milliunits`` (YNAB's invariant).

    ``cleared`` defaults to ``"uncleared"`` to match YNAB's default; pass
    ``"cleared"`` or ``"reconciled"`` when a test needs to exercise tracking
    behavior that is gated on cleared status.
    """
    kwargs: dict[str, Any] = {
        "account_id": account_id,
        "var_date": date_,
        "amount": amount_milliunits,
        "memo": memo,
        "cleared": cleared,
        "approved": False,
    }
    if payee_name is not None:
        kwargs["payee_name"] = payee_name
    if payee_id is not None:
        kwargs["payee_id"] = payee_id
    if subtransactions:
        kwargs["subtransactions"] = list(subtransactions)
    return ynab.NewTransaction(**kwargs)


def build_sub_transaction(
    *,
    amount_milliunits: int,
    memo: str | None = None,
    payee_name: str | None = None,
) -> Any:
    """Construct a ``ynab.SaveSubTransaction`` payload."""
    kwargs: dict[str, Any] = {"amount": amount_milliunits, "memo": memo}
    if payee_name is not None:
        kwargs["payee_name"] = payee_name
    return ynab.SaveSubTransaction(**kwargs)


HKD_ACCOUNT_NAME = "HKD Integration"
HKD_SECONDARY_ACCOUNT_NAME = "HKD Integration 2"
GBP_ACCOUNT_NAME = "GBP Integration"
MANAGED_INTEGRATION_ACCOUNT_NAMES = (
    HKD_ACCOUNT_NAME,
    HKD_SECONDARY_ACCOUNT_NAME,
    GBP_ACCOUNT_NAME,
)

HKD_RATE_TEXT = "7.8"
GBP_RATE_TEXT = "1.35"


@dataclass(frozen=True)
class IntegrationAccountPlan:
    """Which of the expected accounts are available in the live test plan."""

    hkd_primary: RemoteAccount
    hkd_secondary: RemoteAccount | None
    gbp: RemoteAccount


class IntegrationAccountProvisioner(Protocol):
    def create_account(
        self,
        plan_id: str,
        *,
        name: str,
        account_type: Any = ynab.AccountType.CHECKING,
        balance_milliunits: int = 0,
    ) -> RemoteAccount: ...

    def list_accounts(self, plan_id: str) -> AccountSnapshot: ...


def ensure_integration_accounts(
    gateway: IntegrationAccountProvisioner,
    plan_id: str,
    accounts: Sequence[RemoteAccount],
) -> tuple[RemoteAccount, ...]:
    """Ensure the live test plan contains the named accounts the suite expects.

    The dedicated integration budget is a long-lived sandbox. The harness
    provisions missing open accounts on demand so the live suite does not depend
    on any checked-in config file or one-time manual account creation.
    """
    _reject_duplicate_named_accounts(accounts)

    open_accounts = tuple(
        account for account in accounts if not account.deleted and not account.closed
    )
    open_names = {account.name for account in open_accounts}
    missing_names = [
        account_name
        for account_name in MANAGED_INTEGRATION_ACCOUNT_NAMES
        if account_name not in open_names
    ]
    if not missing_names:
        return open_accounts

    _reject_closed_name_conflicts(accounts, missing_names)

    for account_name in missing_names:
        gateway.create_account(plan_id, name=account_name)

    refreshed_accounts = gateway.list_accounts(plan_id).accounts
    refreshed_open_accounts = tuple(
        account for account in refreshed_accounts if not account.deleted and not account.closed
    )
    refreshed_open_names = {account.name for account in refreshed_open_accounts}
    still_missing = [
        account_name
        for account_name in MANAGED_INTEGRATION_ACCOUNT_NAMES
        if account_name not in refreshed_open_names
    ]
    if still_missing:
        missing_text = ", ".join(repr(name) for name in still_missing)
        raise RuntimeError(
            "Integration harness failed to provision required accounts "
            f"{missing_text} in the dedicated test plan."
        )

    return refreshed_open_accounts


def resolve_integration_accounts(
    accounts: Sequence[RemoteAccount],
) -> IntegrationAccountPlan:
    """Return the expected test-plan accounts after harness provisioning."""
    by_name = {account.name: account for account in accounts}
    try:
        hkd_primary = by_name[HKD_ACCOUNT_NAME]
    except KeyError as exc:
        raise RuntimeError(
            f"Integration test plan is missing required account "
            f"{HKD_ACCOUNT_NAME!r}. See docs/testing.md for setup."
        ) from exc
    try:
        gbp = by_name[GBP_ACCOUNT_NAME]
    except KeyError as exc:
        raise RuntimeError(
            f"Integration test plan is missing required account "
            f"{GBP_ACCOUNT_NAME!r}. See docs/testing.md for setup."
        ) from exc
    return IntegrationAccountPlan(
        hkd_primary=hkd_primary,
        hkd_secondary=by_name.get(HKD_SECONDARY_ACCOUNT_NAME),
        gbp=gbp,
    )


def _map_remote_account(raw_account: Any) -> RemoteAccount:
    return RemoteAccount(
        id=str(raw_account.id),
        name=str(raw_account.name),
        deleted=bool(raw_account.deleted),
        closed=bool(raw_account.closed),
    )


def _reject_duplicate_named_accounts(accounts: Sequence[RemoteAccount]) -> None:
    for account_name in MANAGED_INTEGRATION_ACCOUNT_NAMES:
        matches = [
            account
            for account in accounts
            if account.name == account_name and not account.deleted
        ]
        if len(matches) <= 1:
            continue
        raise RuntimeError(
            "Integration test plan contains multiple non-deleted accounts named "
            f"{account_name!r}. Rename or delete the extras before running the "
            "live suite."
        )


def _reject_closed_name_conflicts(
    accounts: Sequence[RemoteAccount], missing_names: Sequence[str]
) -> None:
    conflicting_names = sorted(
        account_name
        for account_name in missing_names
        if any(
            account.name == account_name and not account.deleted and account.closed
            for account in accounts
        )
    )
    if not conflicting_names:
        return

    conflict_text = ", ".join(repr(name) for name in conflicting_names)
    raise RuntimeError(
        "Integration test plan contains closed accounts named "
        f"{conflict_text}. Reopen or rename them manually before running the "
        "live suite; creating duplicates would break account binding."
    )


def apply_account_tracking(plan_config: PlanConfig, tracked_aliases: set[str]) -> PlanConfig:
    """Return ``plan_config`` with ``track_local_balance`` set per alias."""
    return PlanConfig(
        alias=plan_config.alias,
        name=plan_config.name,
        base_currency=plan_config.base_currency,
        accounts=tuple(
            AccountConfig(
                alias=account.alias,
                name=account.name,
                currency=account.currency,
                enabled=account.enabled,
                track_local_balance=account.alias in tracked_aliases,
            )
            for account in plan_config.accounts
        ),
        fx_rates=plan_config.fx_rates,
    )


def build_plan_config(plan_name: str, account_plan: IntegrationAccountPlan) -> PlanConfig:
    """Build a :class:`PlanConfig` that matches the live test plan.

    The returned config is what would come out of :func:`ymca.config.load_config`
    if the user had a hand-written YAML file pointing at the same accounts; we
    construct it directly to keep integration tests hermetic.
    """
    accounts: list[AccountConfig] = [
        AccountConfig(
            alias="hkd_main",
            name=account_plan.hkd_primary.name,
            currency="HKD",
            enabled=True,
        ),
        AccountConfig(
            alias="gbp_main",
            name=account_plan.gbp.name,
            currency="GBP",
            enabled=True,
        ),
    ]
    if account_plan.hkd_secondary is not None:
        accounts.append(
            AccountConfig(
                alias="hkd_secondary",
                name=account_plan.hkd_secondary.name,
                currency="HKD",
                enabled=True,
            )
        )
    fx_rates = {
        "HKD": FxRule(
            rate=Decimal(HKD_RATE_TEXT),
            rate_text=HKD_RATE_TEXT,
            divide_to_base=True,
        ),
        "GBP": FxRule(
            rate=Decimal(GBP_RATE_TEXT),
            rate_text=GBP_RATE_TEXT,
            divide_to_base=False,
        ),
    }
    return PlanConfig(
        alias="integration",
        name=plan_name,
        base_currency="USD",
        accounts=tuple(accounts),
        fx_rates=fx_rates,
    )


def empty_app_state() -> AppState:
    """Return an :class:`AppState` suitable for integration runs.

    Integration tests don't persist state to disk; they build a fresh empty
    state each run so the ``bootstrap`` code paths are consistently exercised.
    """
    return AppState(version=1, plans={})


def find_transactions_by_payee_names(
    raw_transactions: Sequence[Any], payee_names: Sequence[str]
) -> list[Any]:
    """Filter raw SDK transactions to those whose payee name is in ``payee_names``."""
    expected_names = set(payee_names)
    return [
        transaction
        for transaction in raw_transactions
        if getattr(transaction, "payee_name", None) in expected_names
    ]


def transaction_ids_by_payee_name(transactions: Sequence[Any]) -> dict[str, str]:
    """Return transaction ids keyed by payee name."""
    ids_by_payee_name: dict[str, str] = {}
    for transaction in transactions:
        payee_name = getattr(transaction, "payee_name", None)
        if payee_name is None:
            continue
        ids_by_payee_name[str(payee_name)] = str(transaction.id)
    return ids_by_payee_name
