from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from tests.integration.helpers import (
    GBP_ACCOUNT_NAME,
    HKD_ACCOUNT_NAME,
    HKD_SECONDARY_ACCOUNT_NAME,
    IntegrationCleanupError,
    clear_active_plan_transactions,
    ensure_integration_accounts,
    find_transactions_by_payee_names,
    transaction_ids_by_payee_name,
)
from ymca.models import AccountSnapshot, RemoteAccount


@dataclass
class _FakeProvisioner:
    refreshed_accounts: tuple[RemoteAccount, ...]
    created: list[tuple[str, str]] = field(default_factory=list)
    list_accounts_calls: list[str] = field(default_factory=list)

    def create_account(
        self,
        plan_id: str,
        *,
        name: str,
        account_type: object = object(),
        balance_milliunits: int = 0,
    ) -> RemoteAccount:
        del account_type, balance_milliunits
        self.created.append((plan_id, name))
        for account in self.refreshed_accounts:
            if account.name == name:
                return account
        return _open_account(f"created-{name}", name)

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        self.list_accounts_calls.append(plan_id)
        return AccountSnapshot(accounts=self.refreshed_accounts, server_knowledge=1)


class _FakeCleanupError(RuntimeError):
    def __init__(self, *, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class _FakeCleanupGateway:
    active_ids: list[str]
    delete_failures: dict[str, list[BaseException]] = field(default_factory=dict)
    list_failures: list[BaseException] = field(default_factory=list)
    delete_calls: list[str] = field(default_factory=list)
    list_calls: int = 0

    def list_plan_transactions_raw(self, plan_id: str) -> list[SimpleNamespace]:
        del plan_id
        self.list_calls += 1
        if self.list_failures:
            raise self.list_failures.pop(0)
        return [
            SimpleNamespace(id=transaction_id, deleted=False)
            for transaction_id in self.active_ids
        ]

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None:
        del plan_id
        self.delete_calls.append(transaction_id)
        failures = self.delete_failures.get(transaction_id)
        if failures:
            raise failures.pop(0)
        self.active_ids = [
            active_id for active_id in self.active_ids if active_id != transaction_id
        ]


def _open_account(account_id: str, name: str) -> RemoteAccount:
    return RemoteAccount(id=account_id, name=name, deleted=False, closed=False)


def _closed_account(account_id: str, name: str) -> RemoteAccount:
    return RemoteAccount(id=account_id, name=name, deleted=False, closed=True)


def test_ensure_integration_accounts_creates_missing_accounts_and_refreshes() -> None:
    refreshed_accounts = (
        _open_account("acct-hkd-1", HKD_ACCOUNT_NAME),
        _open_account("acct-hkd-2", HKD_SECONDARY_ACCOUNT_NAME),
        _open_account("acct-gbp", GBP_ACCOUNT_NAME),
    )
    gateway = _FakeProvisioner(refreshed_accounts=refreshed_accounts)

    ensured_accounts = ensure_integration_accounts(
        gateway,
        "plan-1",
        (_open_account("other", "Other Account"),),
    )

    assert ensured_accounts == refreshed_accounts
    assert gateway.created == [
        ("plan-1", HKD_ACCOUNT_NAME),
        ("plan-1", HKD_SECONDARY_ACCOUNT_NAME),
        ("plan-1", GBP_ACCOUNT_NAME),
    ]
    assert gateway.list_accounts_calls == ["plan-1"]


def test_ensure_integration_accounts_returns_existing_open_accounts_without_writes() -> None:
    existing_accounts = (
        _open_account("acct-hkd-1", HKD_ACCOUNT_NAME),
        _open_account("acct-hkd-2", HKD_SECONDARY_ACCOUNT_NAME),
        _open_account("acct-gbp", GBP_ACCOUNT_NAME),
    )
    gateway = _FakeProvisioner(refreshed_accounts=())

    ensured_accounts = ensure_integration_accounts(gateway, "plan-1", existing_accounts)

    assert ensured_accounts == existing_accounts
    assert gateway.created == []
    assert gateway.list_accounts_calls == []


def test_ensure_integration_accounts_rejects_closed_name_conflicts() -> None:
    gateway = _FakeProvisioner(refreshed_accounts=())

    with pytest.raises(RuntimeError, match="closed accounts named"):
        ensure_integration_accounts(
            gateway,
            "plan-1",
            (
                _closed_account("acct-hkd-1", HKD_ACCOUNT_NAME),
                _open_account("acct-gbp", GBP_ACCOUNT_NAME),
            ),
        )

    assert gateway.created == []
    assert gateway.list_accounts_calls == []


def test_ensure_integration_accounts_rejects_duplicate_non_deleted_names() -> None:
    gateway = _FakeProvisioner(refreshed_accounts=())

    with pytest.raises(RuntimeError, match="multiple non-deleted accounts named"):
        ensure_integration_accounts(
            gateway,
            "plan-1",
            (
                _open_account("acct-gbp-1", GBP_ACCOUNT_NAME),
                _open_account("acct-gbp-2", GBP_ACCOUNT_NAME),
            ),
        )

    assert gateway.created == []
    assert gateway.list_accounts_calls == []


def test_find_transactions_by_payee_names_filters_expected_rows() -> None:
    transactions = (
        SimpleNamespace(id="txn-1", payee_name="keep me"),
        SimpleNamespace(id="txn-2", payee_name="ignore me"),
        SimpleNamespace(id="txn-3", payee_name="keep me too"),
    )

    matching = find_transactions_by_payee_names(
        transactions,
        ("keep me", "keep me too"),
    )

    assert [transaction.id for transaction in matching] == ["txn-1", "txn-3"]


def test_transaction_ids_by_payee_name_returns_ids_keyed_by_payee_name() -> None:
    transactions = (
        SimpleNamespace(id="txn-1", payee_name="first"),
        SimpleNamespace(id="txn-2", payee_name="second"),
        SimpleNamespace(id="txn-3", payee_name=None),
    )

    assert transaction_ids_by_payee_name(transactions) == {
        "first": "txn-1",
        "second": "txn-2",
    }


def test_clear_active_plan_transactions_retries_transient_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _FakeCleanupGateway(
        active_ids=["txn-1", "txn-2"],
        delete_failures={
            "txn-1": [_FakeCleanupError(status=500, reason="Internal Server Error")]
        },
    )

    monkeypatch.setattr("tests.integration.helpers.time.sleep", lambda _: None)
    clear_active_plan_transactions(
        gateway,
        "plan-1",
        max_sweeps=2,
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )

    assert gateway.active_ids == []
    assert gateway.delete_calls == ["txn-1", "txn-1", "txn-2"]
    assert gateway.list_calls == 2


def test_clear_active_plan_transactions_retries_transient_list_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _FakeCleanupGateway(
        active_ids=["txn-1"],
        list_failures=[_FakeCleanupError(status=500, reason="Internal Server Error")],
    )

    monkeypatch.setattr("tests.integration.helpers.time.sleep", lambda _: None)
    clear_active_plan_transactions(
        gateway,
        "plan-1",
        max_sweeps=2,
        retry_attempts=2,
        retry_backoff_seconds=0.0,
    )

    assert gateway.active_ids == []
    assert gateway.list_calls == 3


def test_clear_active_plan_transactions_raises_if_rows_still_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = _FakeCleanupGateway(
        active_ids=["txn-1"],
        delete_failures={
            "txn-1": [
                _FakeCleanupError(status=500, reason="Internal Server Error"),
                _FakeCleanupError(status=500, reason="Internal Server Error"),
                _FakeCleanupError(status=500, reason="Internal Server Error"),
                _FakeCleanupError(status=500, reason="Internal Server Error"),
            ]
        },
    )

    monkeypatch.setattr("tests.integration.helpers.time.sleep", lambda _: None)
    with pytest.raises(IntegrationCleanupError, match="1 active transaction"):
        clear_active_plan_transactions(
            gateway,
            "plan-1",
            max_sweeps=2,
            retry_attempts=2,
            retry_backoff_seconds=0.0,
        )

    assert gateway.active_ids == ["txn-1"]
