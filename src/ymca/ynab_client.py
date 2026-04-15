from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import ynab
from ynab.rest import ApiException  # type: ignore[attr-defined]

from .errors import ApiError
from .models import (
    AccountSnapshot,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    TransactionSnapshot,
    TransactionUpdateRequest,
)


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
        plans_api = self._require_api(self._plans_api, "PlansApi")
        try:
            response = plans_api.get_plans(include_accounts=include_accounts)
        except ApiException as exc:
            raise ApiError(_format_api_exception("list plans", exc)) from exc

        return tuple(self._map_plan(plan) for plan in response.data.plans)

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        accounts_api = self._require_api(self._accounts_api, "AccountsApi")
        try:
            response = accounts_api.get_accounts(plan_id)
        except ApiException as exc:
            raise ApiError(_format_api_exception("list accounts", exc)) from exc

        return AccountSnapshot(
            accounts=tuple(self._map_account(account) for account in response.data.accounts),
            server_knowledge=int(response.data.server_knowledge),
        )

    def list_transactions(
        self,
        plan_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        try:
            response = transactions_api.get_transactions(
                plan_id,
                since_date=since_date,
                last_knowledge_of_server=last_knowledge_of_server,
            )
        except ApiException as exc:
            raise ApiError(_format_api_exception("list transactions", exc)) from exc

        return TransactionSnapshot(
            transactions=tuple(
                self._map_transaction(transaction) for transaction in response.data.transactions
            ),
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
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        try:
            response = transactions_api.get_transactions_by_account(
                plan_id,
                account_id,
                since_date=since_date,
                last_knowledge_of_server=last_knowledge_of_server,
            )
        except ApiException as exc:
            raise ApiError(_format_api_exception("list account transactions", exc)) from exc

        return TransactionSnapshot(
            transactions=tuple(
                self._map_transaction(transaction) for transaction in response.data.transactions
            ),
            server_knowledge=int(response.data.server_knowledge),
        )

    def get_transaction_detail(self, plan_id: str, transaction_id: str) -> RemoteTransactionDetail:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        try:
            response = transactions_api.get_transaction_by_id(plan_id, transaction_id)
        except ApiException as exc:
            raise ApiError(_format_api_exception("get transaction detail", exc)) from exc

        return self._map_transaction_detail(response.data.transaction)

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        payload = ynab.PutTransactionWrapper(
            transaction=ynab.ExistingTransaction(
                amount=request.amount_milliunits,
                memo=request.memo,
            )
        )
        try:
            transactions_api.update_transaction(plan_id, request.transaction_id, payload)
        except ApiException as exc:
            raise ApiError(_format_api_exception("update transaction", exc)) from exc

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None:
        if not requests:
            return

        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
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
            raise ApiError(_format_api_exception("update transactions", exc)) from exc

    def _map_plan(self, raw_plan: Any) -> RemotePlan:
        raw_accounts = getattr(raw_plan, "accounts", None) or []
        return RemotePlan(
            id=str(raw_plan.id),
            name=str(raw_plan.name),
            accounts=tuple(self._map_account(account) for account in raw_accounts),
        )

    def _map_account(self, raw_account: Any) -> RemoteAccount:
        return RemoteAccount(
            id=str(raw_account.id),
            name=str(raw_account.name),
            deleted=bool(raw_account.deleted),
        )

    def _map_transaction(self, raw_transaction: Any) -> RemoteTransaction:
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

    def _map_transaction_detail(self, raw_transaction: Any) -> RemoteTransactionDetail:
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
        )

    def _require_api(self, api: Any | None, api_name: str) -> Any:
        if api is None:
            raise ApiError(
                f"{api_name} is not initialized. Open YnabClient with a context manager first."
            )
        return api


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _require_date(value: Any, field_name: str) -> date:
    if not isinstance(value, date):
        raise ApiError(f"Unexpected YNAB value for {field_name}: expected date, got {value!r}.")
    return value


def _format_api_exception(action: str, exc: ApiException) -> str:
    status = getattr(exc, "status", None)
    reason = getattr(exc, "reason", None)
    body = getattr(exc, "body", None)
    details = [f"Failed to {action} via YNAB API."]
    if status is not None:
        details.append(f"status={status}")
    if reason:
        details.append(f"reason={reason}")
    if body:
        details.append(f"body={body}")
    return " ".join(details)
