from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from itertools import count
from typing import Literal

from ymca.errors import ApiError
from ymca.models import (
    AccountSnapshot,
    ClearedStatus,
    NewTransactionRequest,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    TransactionSnapshot,
    TransactionUpdateRequest,
)


@dataclass
class SimulatedAccount:
    id: str
    name: str
    deleted: bool = False
    closed: bool = False


@dataclass
class SimulatedTransaction:
    id: str
    date: date
    amount_milliunits: int
    memo: str | None
    account_id: str
    transfer_account_id: str | None = None
    transfer_transaction_id: str | None = None
    deleted: bool = False
    payee_name: str | None = None
    cleared: ClearedStatus = "uncleared"
    subtransaction_count: int = 0
    flag_color: str | None = None
    modified_knowledge: int = 1


@dataclass
class InMemoryGateway:
    plan_id: str
    plan_name: str
    accounts: tuple[SimulatedAccount, ...]
    transactions: Iterable[SimulatedTransaction] = ()
    updates: list[TransactionUpdateRequest] = field(default_factory=list)
    update_batches: list[tuple[str, tuple[TransactionUpdateRequest, ...]]] = field(
        default_factory=list
    )
    created_transactions: list[tuple[str, NewTransactionRequest]] = field(
        default_factory=list
    )
    deleted_transactions: list[tuple[str, str]] = field(default_factory=list)
    list_transactions_by_account_calls: list[
        tuple[str, str, date | None, int | None]
    ] = field(default_factory=list)
    _create_ids: count[int] = field(default_factory=lambda: count(start=1))

    def __post_init__(self) -> None:
        self._accounts_by_id = {account.id: account for account in self.accounts}
        self._transactions_by_id = {
            transaction.id: transaction for transaction in self.transactions
        }
        self._server_knowledge = 1
        for transaction in self._transactions_by_id.values():
            self._server_knowledge = max(self._server_knowledge, transaction.modified_knowledge)

    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]:
        accounts = self._remote_accounts() if include_accounts else ()
        return (RemotePlan(id=self.plan_id, name=self.plan_name, accounts=accounts),)

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        self._require_plan(plan_id)
        return AccountSnapshot(
            accounts=self._remote_accounts(),
            server_knowledge=self._server_knowledge,
        )

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot:
        self._require_plan(plan_id)
        self.list_transactions_by_account_calls.append(
            (plan_id, account_id, since_date, last_knowledge_of_server)
        )
        selected = [
            self._to_remote_transaction(transaction)
            for transaction in self._transactions_by_id.values()
            if transaction.account_id == account_id
            and self._include_transaction(
                transaction,
                since_date=since_date,
                last_knowledge_of_server=last_knowledge_of_server,
            )
        ]
        selected.sort(key=lambda item: (item.date, item.id))
        return TransactionSnapshot(
            transactions=tuple(selected),
            server_knowledge=self._server_knowledge,
        )

    def get_transaction_detail(
        self, plan_id: str, transaction_id: str
    ) -> RemoteTransactionDetail:
        self._require_plan(plan_id)
        transaction = self._transactions_by_id.get(transaction_id)
        if transaction is None:
            raise ApiError(
                "Failed to get transaction detail via YNAB API. status=404",
                status=404,
            )
        return self._to_remote_transaction_detail(transaction)

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        self._require_plan(plan_id)
        self._apply_update_request(request)
        self.updates.append(request)

    def update_transactions(
        self,
        plan_id: str,
        requests: tuple[TransactionUpdateRequest, ...] | list[TransactionUpdateRequest],
    ) -> None:
        self._require_plan(plan_id)
        request_batch = tuple(requests)
        self.update_batches.append((plan_id, request_batch))
        for request in request_batch:
            self._apply_update_request(request)
        self.updates.extend(request_batch)

    def create_transaction(self, plan_id: str, request: NewTransactionRequest) -> str:
        self._require_plan(plan_id)
        self.created_transactions.append((plan_id, request))
        transaction_id = f"created-{next(self._create_ids)}"
        self._server_knowledge += 1
        self._transactions_by_id[transaction_id] = SimulatedTransaction(
            id=transaction_id,
            date=request.date,
            amount_milliunits=request.amount_milliunits,
            memo=request.memo,
            account_id=request.account_id,
            deleted=False,
            payee_name=request.payee_name,
            cleared=request.cleared,
            flag_color=request.flag_color,
            modified_knowledge=self._server_knowledge,
        )
        return transaction_id

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None:
        self._require_plan(plan_id)
        transaction = self._transactions_by_id.get(transaction_id)
        if transaction is None:
            raise ApiError("Failed to delete transaction via YNAB API. status=404", status=404)
        self._server_knowledge += 1
        transaction.deleted = True
        transaction.modified_knowledge = self._server_knowledge
        self.deleted_transactions.append((plan_id, transaction_id))

    def detail(self, transaction_id: str) -> RemoteTransactionDetail:
        return self.get_transaction_detail(self.plan_id, transaction_id)

    def find_active_transaction_by_payee(
        self, payee_name: str, *, account_id: str | None = None
    ) -> RemoteTransactionDetail:
        matches = [
            transaction
            for transaction in self._transactions_by_id.values()
            if transaction.payee_name == payee_name
            and not transaction.deleted
            and (account_id is None or transaction.account_id == account_id)
        ]
        if len(matches) != 1:
            raise AssertionError(
                "Expected exactly one active transaction for payee "
                f"{payee_name!r}, found {len(matches)}."
            )
        return self._to_remote_transaction_detail(matches[0])

    def _apply_update_request(self, request: TransactionUpdateRequest) -> None:
        transaction = self._transactions_by_id.get(request.transaction_id)
        if transaction is None or transaction.deleted:
            raise ApiError(
                "Failed to update transaction via YNAB API. status=400",
                status=400,
            )
        self._server_knowledge += 1
        if request.amount_milliunits is not None:
            transaction.amount_milliunits = request.amount_milliunits
        transaction.memo = request.memo
        request_flag_color = getattr(request, "flag_color", None)
        if request_flag_color is not None:
            transaction.flag_color = request_flag_color
        transaction.modified_knowledge = self._server_knowledge

    def _include_transaction(
        self,
        transaction: SimulatedTransaction,
        *,
        since_date: date | None,
        last_knowledge_of_server: int | None,
    ) -> bool:
        if since_date is not None:
            return transaction.date >= since_date
        if last_knowledge_of_server is not None:
            return transaction.modified_knowledge > last_knowledge_of_server
        return True

    def _remote_accounts(self) -> tuple[RemoteAccount, ...]:
        return tuple(
            RemoteAccount(
                id=account.id,
                name=account.name,
                deleted=account.deleted,
                closed=account.closed,
                cleared_balance_milliunits=self._cleared_balance_for_account(account.id),
            )
            for account in self.accounts
        )

    def _cleared_balance_for_account(self, account_id: str) -> int:
        total = 0
        for transaction in self._transactions_by_id.values():
            if transaction.account_id != account_id or transaction.deleted:
                continue
            if transaction.cleared not in {"cleared", "reconciled"}:
                continue
            total += transaction.amount_milliunits
        return total

    def _to_remote_transaction(
        self, transaction: SimulatedTransaction
    ) -> RemoteTransaction:
        return RemoteTransaction(
            id=transaction.id,
            date=transaction.date,
            amount_milliunits=transaction.amount_milliunits,
            memo=transaction.memo,
            account_id=transaction.account_id,
            transfer_account_id=transaction.transfer_account_id,
            transfer_transaction_id=transaction.transfer_transaction_id,
            deleted=transaction.deleted,
            payee_name=transaction.payee_name,
            cleared=transaction.cleared,
        )

    def _to_remote_transaction_detail(
        self, transaction: SimulatedTransaction
    ) -> RemoteTransactionDetail:
        return RemoteTransactionDetail(
            id=transaction.id,
            date=transaction.date,
            amount_milliunits=transaction.amount_milliunits,
            memo=transaction.memo,
            account_id=transaction.account_id,
            transfer_account_id=transaction.transfer_account_id,
            transfer_transaction_id=transaction.transfer_transaction_id,
            deleted=transaction.deleted,
            subtransaction_count=transaction.subtransaction_count,
            payee_name=transaction.payee_name,
            cleared=transaction.cleared,
        )

    def _require_plan(self, plan_id: str) -> None:
        if plan_id != self.plan_id:
            raise ApiError(f"Configured plan {plan_id!r} was not found in YNAB.")


@dataclass
class InMemoryGatewayContext:
    gateway: InMemoryGateway

    def __enter__(self) -> InMemoryGateway:
        return self.gateway

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        del exc_type, exc, traceback
        return False
