from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any
from uuid import UUID

import ynab
from ynab.rest import ApiException  # type: ignore[attr-defined]

from .errors import ApiError
from .models import (
    AccountSnapshot,
    ClearedStatus,
    NewTransactionRequest,
    RemoteAccount,
    RemotePlan,
    RemoteSubTransaction,
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
            raise ApiError(
                _format_api_exception("list plans", exc),
                status=_api_exception_status(exc),
            ) from exc

        return tuple(self._map_plan(plan) for plan in response.data.plans)

    def list_accounts(self, plan_id: str) -> AccountSnapshot:
        accounts_api = self._require_api(self._accounts_api, "AccountsApi")
        try:
            response = accounts_api.get_accounts(plan_id)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("list accounts", exc),
                status=_api_exception_status(exc),
            ) from exc

        return AccountSnapshot(
            accounts=tuple(self._map_account(account) for account in response.data.accounts),
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
            raise ApiError(
                _format_api_exception("list account transactions", exc),
                status=_api_exception_status(exc),
            ) from exc

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
            raise ApiError(
                _format_api_exception("get transaction detail", exc),
                status=_api_exception_status(exc),
            ) from exc

        return self._map_transaction_detail(response.data.transaction)

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
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
                self._build_save_subtransaction(subtransaction)
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

        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        payload = ynab.PatchTransactionsWrapper(
            transactions=[
                self._build_patch_transaction(request) for request in requests
            ]
        )
        try:
            transactions_api.update_transactions(plan_id, payload)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("update transactions", exc),
                status=_api_exception_status(exc),
            ) from exc

    @staticmethod
    def _build_patch_transaction(request: TransactionUpdateRequest) -> Any:
        kwargs: dict[str, Any] = {
            "id": request.transaction_id,
            "amount": request.amount_milliunits,
            "memo": request.memo,
        }
        if request.payee_id is not None:
            kwargs["payee_id"] = UUID(request.payee_id)
        if request.flag_color is not None:
            kwargs["flag_color"] = ynab.TransactionFlagColor(request.flag_color)
        return ynab.SaveTransactionWithIdOrImportId(**kwargs)

    @staticmethod
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

    def create_transaction(self, plan_id: str, request: NewTransactionRequest) -> str:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        new_kwargs: dict[str, Any] = {
            "account_id": UUID(request.account_id),
            "date": request.date,
            "amount": request.amount_milliunits,
            "payee_name": request.payee_name,
            "memo": request.memo,
            "cleared": ynab.TransactionClearedStatus(request.cleared),
        }
        if request.flag_color is not None:
            new_kwargs["flag_color"] = ynab.TransactionFlagColor(request.flag_color)
        payload = ynab.PostTransactionsWrapper(
            transaction=ynab.NewTransaction(**new_kwargs)
        )
        try:
            response = transactions_api.create_transaction(plan_id, payload)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("create transaction", exc),
                status=_api_exception_status(exc),
            ) from exc

        # YNAB's ``SaveTransactionsResponse`` wraps the payload in ``data``.
        data = getattr(response, "data", response)

        # ``transaction_ids`` is the canonical list per the YNAB OpenAPI spec;
        # it is populated for both single-transaction and batch creation.
        transaction_ids = getattr(data, "transaction_ids", None) or []
        if transaction_ids:
            return str(transaction_ids[0])

        transaction = getattr(data, "transaction", None)
        if transaction is not None and getattr(transaction, "id", None) is not None:
            return str(transaction.id)

        created = getattr(data, "transactions", None) or []
        if created:
            return str(created[0].id)
        raise ApiError("Create transaction response did not include a transaction id.")

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None:
        transactions_api = self._require_api(self._transactions_api, "TransactionsApi")
        try:
            transactions_api.delete_transaction(plan_id, transaction_id)
        except ApiException as exc:
            raise ApiError(
                _format_api_exception("delete transaction", exc),
                status=_api_exception_status(exc),
            ) from exc

    def _map_plan(self, raw_plan: Any) -> RemotePlan:
        raw_accounts = getattr(raw_plan, "accounts", None) or []
        return RemotePlan(
            id=str(raw_plan.id),
            name=str(raw_plan.name),
            accounts=tuple(self._map_account(account) for account in raw_accounts),
        )

    def _map_account(self, raw_account: Any) -> RemoteAccount:
        cleared_balance = getattr(raw_account, "cleared_balance", 0)
        return RemoteAccount(
            id=str(raw_account.id),
            name=str(raw_account.name),
            deleted=bool(raw_account.deleted),
            closed=bool(raw_account.closed),
            cleared_balance_milliunits=int(cleared_balance) if cleared_balance is not None else 0,
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
            payee_id=_optional_string(getattr(raw_transaction, "payee_id", None)),
            payee_name=_optional_string(getattr(raw_transaction, "payee_name", None)),
            cleared=_map_cleared(getattr(raw_transaction, "cleared", None)),
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
            payee_id=_optional_string(getattr(raw_transaction, "payee_id", None)),
            payee_name=_optional_string(getattr(raw_transaction, "payee_name", None)),
            category_id=_optional_string(getattr(raw_transaction, "category_id", None)),
            cleared=_map_cleared(getattr(raw_transaction, "cleared", None)),
            approved=bool(getattr(raw_transaction, "approved", False)),
            flag_color=_map_flag_color(getattr(raw_transaction, "flag_color", None)),
            subtransactions=tuple(
                self._map_subtransaction(subtransaction)
                for subtransaction in raw_subtransactions
            ),
        )

    @staticmethod
    def _map_subtransaction(raw_subtransaction: Any) -> RemoteSubTransaction:
        return RemoteSubTransaction(
            amount_milliunits=int(raw_subtransaction.amount),
            payee_id=_optional_string(getattr(raw_subtransaction, "payee_id", None)),
            payee_name=_optional_string(getattr(raw_subtransaction, "payee_name", None)),
            category_id=_optional_string(getattr(raw_subtransaction, "category_id", None)),
            memo=getattr(raw_subtransaction, "memo", None),
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


def _api_exception_status(exc: ApiException) -> int | None:
    raw_status = getattr(exc, "status", None)
    if isinstance(raw_status, int):
        return raw_status
    if isinstance(raw_status, str) and raw_status.isdigit():
        return int(raw_status)
    return None


def _map_cleared(value: Any) -> ClearedStatus:
    """Normalize YNAB's TransactionClearedStatus enum to our Literal."""
    if value is None:
        return "uncleared"
    raw = getattr(value, "value", value)
    text = str(raw).lower()
    if text == "cleared":
        return "cleared"
    if text == "reconciled":
        return "reconciled"
    return "uncleared"


def _map_flag_color(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


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
