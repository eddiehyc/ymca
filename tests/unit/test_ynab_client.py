"""Unit coverage for :mod:`ymca.ynab_client`.

We monkey-patch the ``ynab.*Api`` classes to avoid any real HTTP traffic.
The goal is to cover:

* context-manager lifecycle (``__enter__`` / ``__exit__``).
* Happy-path mapping of SDK responses to internal models.
* Error translation: every ``ApiException`` is wrapped in :class:`ApiError`
  with a formatted message.
* Defensive ``_require_api`` / ``_require_date`` branches.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest
import ynab
from pytest import MonkeyPatch
from ynab.rest import ApiException  # type: ignore[attr-defined]

from ymca import ynab_client as ynab_client_module
from ymca.errors import ApiError
from ymca.models import TransactionUpdateRequest
from ymca.ynab_client import (
    YnabClient,
    _format_api_exception,
    _optional_string,
    _require_date,
)


class _FakeApiException(Exception):
    """Stand-in for :class:`ynab.rest.ApiException` so we don't depend on the SDK's internals."""

    def __init__(
        self,
        *,
        status: int = 500,
        reason: str = "Internal Server Error",
        body: str = "{}",
    ) -> None:
        super().__init__(f"status={status}")
        self.status = status
        self.reason = reason
        self.body = body


class _FakeApiClient:
    """Stand-in for :class:`ynab.ApiClient` that records open/close."""

    def __init__(self, configuration: Any) -> None:
        self.configuration = configuration
        self.entered = 0
        self.exited = 0

    def __enter__(self) -> _FakeApiClient:
        self.entered += 1
        return self

    def __exit__(
        self, exc_type: object, exc: object, traceback: object
    ) -> Literal[False]:
        self.exited += 1
        return False


class _FakeApi:
    """Base class for fake SDK API objects with configurable responses/errors."""

    def __init__(self, api_client: Any) -> None:
        self.api_client = api_client


def _make_plan(id_: str, name: str, accounts: tuple[Any, ...] = ()) -> Any:
    return SimpleNamespace(id=id_, name=name, accounts=accounts)


def _make_account(
    id_: str,
    name: str,
    deleted: bool = False,
    closed: bool = False,
) -> Any:
    return SimpleNamespace(id=id_, name=name, deleted=deleted, closed=closed)


def _make_transaction(
    *,
    id_: str = "t1",
    txn_date: date = date(2026, 1, 1),
    amount: int = -100,
    memo: str | None = "memo",
    account_id: str = "acct",
    transfer_account_id: str | None = None,
    transfer_transaction_id: str | None = None,
    deleted: bool = False,
) -> Any:
    return SimpleNamespace(
        id=id_,
        var_date=txn_date,
        amount=amount,
        memo=memo,
        account_id=account_id,
        transfer_account_id=transfer_account_id,
        transfer_transaction_id=transfer_transaction_id,
        deleted=deleted,
    )


def _install_sdk_fakes(
    monkeypatch: MonkeyPatch,
    *,
    plans_api_cls: type,
    accounts_api_cls: type,
    transactions_api_cls: type,
) -> dict[str, list[Any]]:
    """Replace the ``ynab`` module entry points used by :class:`YnabClient`.

    Returns a dict of recorded instances keyed by api-class name so tests can
    assert which methods were invoked.
    """
    records: dict[str, list[Any]] = {
        "PlansApi": [],
        "AccountsApi": [],
        "TransactionsApi": [],
        "ApiClient": [],
    }

    def recording_plans(api_client: Any) -> Any:
        instance = plans_api_cls(api_client)
        records["PlansApi"].append(instance)
        return instance

    def recording_accounts(api_client: Any) -> Any:
        instance = accounts_api_cls(api_client)
        records["AccountsApi"].append(instance)
        return instance

    def recording_transactions(api_client: Any) -> Any:
        instance = transactions_api_cls(api_client)
        records["TransactionsApi"].append(instance)
        return instance

    def recording_api_client(configuration: Any) -> Any:
        client = _FakeApiClient(configuration)
        records["ApiClient"].append(client)
        return client

    def configuration(access_token: str) -> Any:
        return SimpleNamespace(access_token=access_token)

    monkeypatch.setattr(ynab, "PlansApi", recording_plans)
    monkeypatch.setattr(ynab, "AccountsApi", recording_accounts)
    monkeypatch.setattr(ynab, "TransactionsApi", recording_transactions)
    monkeypatch.setattr(ynab, "ApiClient", recording_api_client)
    monkeypatch.setattr(ynab, "Configuration", configuration)
    monkeypatch.setattr(ynab_client_module, "ApiException", _FakeApiException)
    return records


def test_ynab_client_enter_and_exit_delegate_to_api_client(monkeypatch: MonkeyPatch) -> None:
    records = _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_FakeApi,
    )
    with YnabClient("secret") as client:
        assert client is not None
    assert len(records["ApiClient"]) == 1
    fake_client = records["ApiClient"][0]
    assert fake_client.entered == 1
    assert fake_client.exited == 1


def test_ynab_client_exit_before_enter_returns_false() -> None:
    client = YnabClient("secret")
    assert client.__exit__(None, None, None) is False


def test_ynab_client_list_plans_maps_accounts(monkeypatch: MonkeyPatch) -> None:
    class _PlansApi(_FakeApi):
        def get_plans(self, *, include_accounts: bool = False) -> Any:
            assert include_accounts is True
            return SimpleNamespace(
                data=SimpleNamespace(
                    plans=[
                        _make_plan(
                            "p1",
                            "Example",
                            accounts=(_make_account("a1", "Acct1"),),
                        )
                    ]
                )
            )

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_PlansApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_FakeApi,
    )
    with YnabClient("secret") as client:
        plans = client.list_plans(include_accounts=True)

    assert len(plans) == 1
    assert plans[0].id == "p1"
    assert plans[0].name == "Example"
    assert len(plans[0].accounts) == 1
    assert plans[0].accounts[0].id == "a1"


def test_ynab_client_list_plans_translates_api_exception(monkeypatch: MonkeyPatch) -> None:
    class _PlansApi(_FakeApi):
        def get_plans(self, *, include_accounts: bool = False) -> Any:
            raise _FakeApiException(status=401, reason="Unauthorized", body="bad token")

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_PlansApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_FakeApi,
    )
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc_info:
        client.list_plans()
    message = str(exc_info.value)
    assert "Failed to list plans" in message
    assert "status=401" in message
    assert "reason=Unauthorized" in message
    assert "body=bad token" in message


def test_ynab_client_list_accounts_maps_snapshot(monkeypatch: MonkeyPatch) -> None:
    class _AccountsApi(_FakeApi):
        def get_accounts(self, plan_id: str) -> Any:
            assert plan_id == "p1"
            return SimpleNamespace(
                data=SimpleNamespace(
                    accounts=[_make_account("a1", "Acct1")],
                    server_knowledge=42,
                )
            )

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_AccountsApi,
        transactions_api_cls=_FakeApi,
    )
    with YnabClient("secret") as client:
        snapshot = client.list_accounts("p1")
    assert snapshot.server_knowledge == 42
    assert snapshot.accounts[0].id == "a1"


def test_ynab_client_list_accounts_wraps_exception(monkeypatch: MonkeyPatch) -> None:
    class _AccountsApi(_FakeApi):
        def get_accounts(self, plan_id: str) -> Any:
            raise _FakeApiException(status=500)

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_AccountsApi,
        transactions_api_cls=_FakeApi,
    )
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc:
        client.list_accounts("p1")
    assert "Failed to list accounts" in str(exc.value)


def test_ynab_client_list_transactions_by_account_maps_snapshot(
    monkeypatch: MonkeyPatch,
) -> None:
    class _TransactionsApi(_FakeApi):
        def get_transactions_by_account(
            self,
            plan_id: str,
            account_id: str,
            *,
            since_date: date | None = None,
            last_knowledge_of_server: int | None = None,
        ) -> Any:
            assert plan_id == "p1"
            assert account_id == "a1"
            assert since_date == date(2026, 1, 1)
            assert last_knowledge_of_server == 7
            return SimpleNamespace(
                data=SimpleNamespace(
                    transactions=[_make_transaction()],
                    server_knowledge=99,
                )
            )

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    with YnabClient("secret") as client:
        snapshot = client.list_transactions_by_account(
            "p1",
            "a1",
            since_date=date(2026, 1, 1),
            last_knowledge_of_server=7,
        )
    assert snapshot.server_knowledge == 99
    assert snapshot.transactions[0].id == "t1"


def test_ynab_client_list_transactions_by_account_wraps_exception(
    monkeypatch: MonkeyPatch,
) -> None:
    class _TransactionsApi(_FakeApi):
        def get_transactions_by_account(
            self, *args: Any, **kwargs: Any
        ) -> Any:
            raise _FakeApiException(status=503)

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc:
        client.list_transactions_by_account("p1", "a1")
    assert "list account transactions" in str(exc.value)


def test_ynab_client_get_transaction_detail_maps_subtransaction_count(
    monkeypatch: MonkeyPatch,
) -> None:
    class _TransactionsApi(_FakeApi):
        def get_transaction_by_id(self, plan_id: str, transaction_id: str) -> Any:
            assert plan_id == "p1"
            assert transaction_id == "t1"
            transaction = _make_transaction()
            transaction.subtransactions = [SimpleNamespace(), SimpleNamespace()]
            return SimpleNamespace(data=SimpleNamespace(transaction=transaction))

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    with YnabClient("secret") as client:
        detail = client.get_transaction_detail("p1", "t1")
    assert detail.subtransaction_count == 2


def test_ynab_client_get_transaction_detail_wraps_exception(
    monkeypatch: MonkeyPatch,
) -> None:
    class _TransactionsApi(_FakeApi):
        def get_transaction_by_id(self, *args: Any, **kwargs: Any) -> Any:
            raise _FakeApiException()

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc:
        client.get_transaction_detail("p1", "t1")
    assert "get transaction detail" in str(exc.value)


def test_ynab_client_update_transaction_sends_put_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _TransactionsApi(_FakeApi):
        def update_transaction(
            self, plan_id: str, transaction_id: str, payload: Any
        ) -> Any:
            captured.update(
                plan_id=plan_id,
                transaction_id=transaction_id,
                payload=payload,
            )
            return SimpleNamespace()

    monkeypatch.setattr(
        ynab,
        "PutTransactionWrapper",
        lambda transaction: SimpleNamespace(transaction=transaction),
    )
    monkeypatch.setattr(
        ynab,
        "ExistingTransaction",
        lambda amount, memo: SimpleNamespace(amount=amount, memo=memo),
    )
    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    request = TransactionUpdateRequest(
        transaction_id="t1", amount_milliunits=-500, memo="m"
    )
    with YnabClient("secret") as client:
        client.update_transaction("p1", request)
    assert captured["plan_id"] == "p1"
    assert captured["transaction_id"] == "t1"
    assert captured["payload"].transaction.amount == -500
    assert captured["payload"].transaction.memo == "m"


def test_ynab_client_update_transaction_wraps_exception(monkeypatch: MonkeyPatch) -> None:
    class _TransactionsApi(_FakeApi):
        def update_transaction(self, *args: Any, **kwargs: Any) -> Any:
            raise _FakeApiException()

    monkeypatch.setattr(
        ynab,
        "PutTransactionWrapper",
        lambda transaction: SimpleNamespace(transaction=transaction),
    )
    monkeypatch.setattr(
        ynab,
        "ExistingTransaction",
        lambda amount, memo: SimpleNamespace(amount=amount, memo=memo),
    )
    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    request = TransactionUpdateRequest(
        transaction_id="t1", amount_milliunits=-500, memo="m"
    )
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc:
        client.update_transaction("p1", request)
    assert "update transaction" in str(exc.value)


def test_ynab_client_update_transactions_sends_batch_payload(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class _TransactionsApi(_FakeApi):
        def update_transactions(self, plan_id: str, payload: Any) -> Any:
            captured["plan_id"] = plan_id
            captured["payload"] = payload
            return SimpleNamespace()

    monkeypatch.setattr(
        ynab,
        "PatchTransactionsWrapper",
        lambda transactions: SimpleNamespace(transactions=transactions),
    )
    monkeypatch.setattr(
        ynab,
        "SaveTransactionWithIdOrImportId",
        lambda id, amount, memo: SimpleNamespace(id=id, amount=amount, memo=memo),
    )
    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    requests = (
        TransactionUpdateRequest(transaction_id="t1", amount_milliunits=-1, memo="a"),
        TransactionUpdateRequest(transaction_id="t2", amount_milliunits=-2, memo="b"),
    )
    with YnabClient("secret") as client:
        client.update_transactions("p1", requests)
    assert captured["plan_id"] == "p1"
    assert len(captured["payload"].transactions) == 2
    assert captured["payload"].transactions[0].id == "t1"
    assert captured["payload"].transactions[1].id == "t2"


def test_ynab_client_update_transactions_noop_on_empty(monkeypatch: MonkeyPatch) -> None:
    class _TransactionsApi(_FakeApi):
        called = False

        def update_transactions(self, *args: Any, **kwargs: Any) -> Any:
            _TransactionsApi.called = True
            return SimpleNamespace()

    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    with YnabClient("secret") as client:
        client.update_transactions("p1", ())
    assert _TransactionsApi.called is False


def test_ynab_client_update_transactions_wraps_exception(monkeypatch: MonkeyPatch) -> None:
    class _TransactionsApi(_FakeApi):
        def update_transactions(self, *args: Any, **kwargs: Any) -> Any:
            raise _FakeApiException()

    monkeypatch.setattr(
        ynab,
        "PatchTransactionsWrapper",
        lambda transactions: SimpleNamespace(transactions=transactions),
    )
    monkeypatch.setattr(
        ynab,
        "SaveTransactionWithIdOrImportId",
        lambda id, amount, memo: SimpleNamespace(id=id, amount=amount, memo=memo),
    )
    _install_sdk_fakes(
        monkeypatch,
        plans_api_cls=_FakeApi,
        accounts_api_cls=_FakeApi,
        transactions_api_cls=_TransactionsApi,
    )
    requests = (TransactionUpdateRequest(transaction_id="t1", amount_milliunits=-1, memo="a"),)
    with YnabClient("secret") as client, pytest.raises(ApiError) as exc:
        client.update_transactions("p1", requests)
    assert "update transactions" in str(exc.value)


def test_require_api_raises_when_not_initialized() -> None:
    client = YnabClient("secret")
    with pytest.raises(ApiError) as exc:
        client.list_plans()
    assert "PlansApi is not initialized" in str(exc.value)


def test_require_date_raises_on_non_date() -> None:
    with pytest.raises(ApiError):
        _require_date("not-a-date", "transaction.var_date")


def test_optional_string_returns_none_for_none() -> None:
    assert _optional_string(None) is None
    assert _optional_string(123) == "123"


def test_format_api_exception_handles_minimal_exc() -> None:
    fake = _FakeApiException(status=400, reason="Bad", body="")
    message = _format_api_exception("do stuff", cast(ApiException, fake))
    assert "Failed to do stuff" in message
    assert "status=400" in message
    assert "reason=Bad" in message
