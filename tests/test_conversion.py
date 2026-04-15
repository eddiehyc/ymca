from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.fakes import FakeGateway
from ymca.conversion import build_prepared_conversion, execute_conversion
from ymca.models import (
    AccountConfig,
    AccountSnapshot,
    AppState,
    FxRule,
    PlanConfig,
    PlanState,
    PreparedConversion,
    PreparedUpdate,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    ResolvedBindings,
    SyncRequest,
    TransactionSnapshot,
    TransactionUpdateRequest,
)


def test_build_prepared_conversion_uses_milliunit_precision_and_skips_marked_transactions() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=12,
            )
        },
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=12,
            )
        },
        transaction_details={
            "txn-1": RemoteTransactionDetail(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=12340,
                memo="Dinner",
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Dinner",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                        RemoteTransaction(
                            id="txn-2",
                            date=date(2026, 4, 11),
                            amount_milliunits=1000,
                            memo="[FX] 1.00 HKD (rate: 7.8 HKD/USD)",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                )
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=None,
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert prepared.sync_request.last_knowledge_of_server == 12
    assert prepared.queried_account_ids == ("acct-1",)
    assert len(prepared.updates) == 1
    assert prepared.updates[0].converted_amount_milliunits == 1582
    assert prepared.updates[0].new_memo == "Dinner | [FX] 12.34 HKD (rate: 7.8 HKD/USD)"
    assert len(prepared.skipped) == 1
    assert prepared.skipped[0].reason == "already-converted"


def test_build_prepared_conversion_skips_legacy_marked_transactions() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    state = AppState(version=1, plans={})
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=612490,
                            memo="612.49 HKD (FX rate: 0.12821)",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                )
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert prepared.updates == ()
    assert len(prepared.skipped) == 1
    assert prepared.skipped[0].reason == "legacy-marker"


def test_execute_conversion_saves_follow_up_server_knowledge() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    state = AppState(version=1, plans={})
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            )
        },
        transaction_details={
            "txn-1": RemoteTransactionDetail(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=12340,
                memo=None,
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo=None,
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                ),
                TransactionSnapshot(
                    transactions=(),
                    server_knowledge=55,
                ),
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )
    outcome = execute_conversion(
        prepared=prepared,
        state=state,
        gateway=gateway,
        apply_updates=True,
    )

    assert len(gateway.updates) == 1
    assert len(gateway.update_batches) == 1
    assert len(gateway.update_batches[0][1]) == 1
    assert outcome.writes_performed == 1
    assert outcome.saved_server_knowledge == 55
    assert outcome.new_state.plans["personal"].plan_id == "plan-1"
    assert outcome.new_state.plans["personal"].account_ids["travel_hkd"] == "acct-1"
    assert outcome.new_state.plans["personal"].server_knowledge == 55


def test_execute_conversion_batches_writes_per_account() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
            AccountConfig(alias="cash_hkd", name="Cash HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    state = AppState(version=1, plans={})
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Cash HKD", deleted=False),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=60)],
            "acct-2": [TransactionSnapshot(transactions=(), server_knowledge=61)],
        },
    )
    prepared = PreparedConversion(
        bindings=ResolvedBindings(
            plan=plan,
            plan_id="plan-1",
            account_ids={"travel_hkd": "acct-1", "cash_hkd": "acct-2"},
        ),
        sync_request=SyncRequest(
            last_knowledge_of_server=44,
            since_date=None,
            used_bootstrap=False,
        ),
        queried_account_ids=("acct-1", "acct-2"),
        fetched_transactions=3,
        fetched_server_knowledge=44,
        updates=(
            _prepared_update(
                transaction_id="txn-1",
                account_alias="travel_hkd",
                memo="[FX] 1.00 HKD (rate: 7.8 HKD/USD)",
            ),
            _prepared_update(
                transaction_id="txn-2",
                account_alias="travel_hkd",
                memo="[FX] 2.00 HKD (rate: 7.8 HKD/USD)",
            ),
            _prepared_update(
                transaction_id="txn-3",
                account_alias="cash_hkd",
                memo="[FX] 3.00 HKD (rate: 7.8 HKD/USD)",
            ),
        ),
        skipped=(),
    )

    outcome = execute_conversion(
        prepared=prepared,
        state=state,
        gateway=gateway,
        apply_updates=True,
    )

    assert outcome.writes_performed == 3
    assert len(gateway.update_batches) == 2
    assert gateway.update_batches[0][0] == "plan-1"
    assert tuple(request.transaction_id for request in gateway.update_batches[0][1]) == (
        "txn-1",
        "txn-2",
    )
    assert tuple(request.transaction_id for request in gateway.update_batches[1][1]) == (
        "txn-3",
    )
    assert outcome.saved_server_knowledge == 61


def test_build_prepared_conversion_processes_transfer_once_with_explicit_sign() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
            AccountConfig(alias="cash_hkd", name="Cash HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    state = AppState(version=1, plans={})
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Cash HKD", deleted=False),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={
            "txn-out": RemoteTransactionDetail(
                id="txn-out",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="Move money",
                account_id="acct-1",
                transfer_account_id="acct-2",
                transfer_transaction_id="txn-in",
                deleted=False,
                subtransaction_count=0,
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-out",
                            date=date(2026, 4, 10),
                            amount_milliunits=-12340,
                            memo="Move money",
                            account_id="acct-1",
                            transfer_account_id="acct-2",
                            transfer_transaction_id="txn-in",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                )
            ],
            "acct-2": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-in",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Move money",
                            account_id="acct-2",
                            transfer_account_id="acct-1",
                            transfer_transaction_id="txn-out",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                )
            ],
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert len(prepared.updates) == 1
    assert prepared.updates[0].transaction_id == "txn-out"
    assert prepared.updates[0].is_transfer is True
    assert prepared.updates[0].new_memo == "Move money | [FX] -12.34 HKD (rate: 7.8 HKD/USD)"
    assert len(prepared.skipped) == 1
    assert prepared.skipped[0].transaction_id == "txn-in"
    assert prepared.skipped[0].reason == "paired-transfer"


def _prepared_update(
    *,
    transaction_id: str,
    account_alias: str,
    memo: str,
) -> PreparedUpdate:
    request = TransactionUpdateRequest(
        transaction_id=transaction_id,
        amount_milliunits=1234,
        memo=memo,
    )
    return PreparedUpdate(
        transaction_id=transaction_id,
        date=date(2026, 4, 10),
        account_alias=account_alias,
        account_name=account_alias,
        is_transfer=False,
        source_currency="HKD",
        source_amount_milliunits=12340,
        converted_currency="USD",
        converted_amount_milliunits=1582,
        rate_text="7.8",
        pair_label="HKD/USD",
        old_memo=None,
        new_memo=memo,
        request=request,
    )
