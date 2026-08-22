from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from itertools import count
from typing import Literal

from ymca.errors import ApiError
from ymca.models import (
    AccountSnapshot,
    NewTransactionRequest,
    RemotePlan,
    RemoteTransactionDetail,
    TransactionSnapshot,
    TransactionUpdateRequest,
)


@dataclass
class FakeGateway:
    plans: tuple[RemotePlan, ...]
    account_snapshots: dict[str, AccountSnapshot]
    transaction_details: dict[str, RemoteTransactionDetail]
    transaction_snapshots_by_account: dict[str, list[TransactionSnapshot]] = field(
        default_factory=dict
    )
    list_plans_error: ApiError | None = None
    updates: list[TransactionUpdateRequest] = field(default_factory=list)
    update_batches: list[tuple[str, tuple[TransactionUpdateRequest, ...]]] = field(
        default_factory=list
    )
    list_transactions_by_account_calls: list[tuple[str, str, date | None, int | None]] = field(
        default_factory=list
    )
    created_transactions: list[tuple[str, NewTransactionRequest]] = field(default_factory=list)
    deleted_transactions: list[tuple[str, str]] = field(default_factory=list)
    create_transaction_ids: list[str] = field(default_factory=list)
    _generated_create_ids: count[int] = field(default_factory=lambda: count(start=1))
    request_count: int = 0
    request_times: list[datetime] = field(default_factory=list)

    def _record_request(self) -> None:
        self.request_count += 1
        self.request_times.append(datetime.now(UTC))

    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]:
        del include_accounts
        self._record_request()
        if self.list_plans_error is not None:
            raise self.list_plans_error
        return self.plans

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        self._record_request()
        return self.account_snapshots[plan_id]

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot:
        self._record_request()
        self.list_transactions_by_account_calls.append(
            (plan_id, account_id, since_date, last_knowledge_of_server)
        )
        snapshots = self.transaction_snapshots_by_account.get(account_id)
        if not snapshots:
            raise AssertionError(f"No account transaction snapshot prepared for {account_id}.")
        return snapshots.pop(0)

    def get_transaction_detail(self, plan_id: str, transaction_id: str) -> RemoteTransactionDetail:
        del plan_id
        self._record_request()
        return self.transaction_details[transaction_id]

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        del plan_id
        self._record_request()
        self.updates.append(request)

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None:
        if not requests:
            return
        self._record_request()
        request_batch = tuple(requests)
        self.update_batches.append((plan_id, request_batch))
        self.updates.extend(request_batch)

    def create_transaction(self, plan_id: str, request: NewTransactionRequest) -> str:
        self._record_request()
        self.created_transactions.append((plan_id, request))
        if self.create_transaction_ids:
            return self.create_transaction_ids.pop(0)
        return f"fake-created-{next(self._generated_create_ids)}"

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None:
        self._record_request()
        self.deleted_transactions.append((plan_id, transaction_id))


@dataclass
class FakeGatewayContext:
    gateway: FakeGateway

    def __enter__(self) -> FakeGateway:
        return self.gateway

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        del exc_type, exc, traceback
        return False
