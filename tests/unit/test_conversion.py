from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.fakes import FakeGateway
from ymca.conversion import build_prepared_conversion, execute_conversion, resolve_bindings
from ymca.errors import ApiError, ConfigError, UserInputError
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


def test_build_prepared_conversion_bootstrap_since_overrides_saved_server_knowledge() -> None:
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
                server_knowledge=117506,
            )
        },
    )
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
                    transactions=(),
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
        bootstrap_since=date(2025, 1, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert prepared.sync_request.used_bootstrap is True
    assert prepared.sync_request.since_date == date(2025, 1, 1)
    assert prepared.sync_request.last_knowledge_of_server is None
    assert gateway.list_transactions_by_account_calls == [
        ("plan-1", "acct-1", date(2025, 1, 1), None),
    ]


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


def test_build_prepared_conversion_processes_transfer_once_with_plus_minus_prefix() -> None:
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
    assert prepared.updates[0].new_memo == "Move money | [FX] +/-12.34 HKD (rate: 7.8 HKD/USD)"
    assert len(prepared.skipped) == 1
    assert prepared.skipped[0].transaction_id == "txn-in"
    assert prepared.skipped[0].reason == "paired-transfer"


def test_build_prepared_conversion_marks_partial_tracked_transfer_with_arrow() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(
                alias="wallet_out",
                name="Wallet Out",
                currency="HKD",
                enabled=True,
                track_local_balance=True,
            ),
            AccountConfig(
                alias="wallet_in",
                name="Wallet In",
                currency="HKD",
                enabled=True,
                track_local_balance=True,
            ),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-out", name="Wallet Out", deleted=False),
                    RemoteAccount(id="acct-in", name="Wallet In", deleted=False),
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
                account_id="acct-out",
                transfer_account_id="acct-in",
                transfer_transaction_id="txn-in",
                deleted=False,
                subtransaction_count=0,
                cleared="cleared",
            )
        },
        transaction_snapshots_by_account={
            "acct-out": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-out",
                            date=date(2026, 4, 10),
                            amount_milliunits=-12340,
                            memo="Move money",
                            account_id="acct-out",
                            transfer_account_id="acct-in",
                            transfer_transaction_id="txn-in",
                            deleted=False,
                            cleared="cleared",
                        ),
                    ),
                    server_knowledge=44,
                )
            ],
            "acct-in": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-in",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Move money",
                            account_id="acct-in",
                            transfer_account_id="acct-out",
                            transfer_transaction_id="txn-out",
                            deleted=False,
                            cleared="uncleared",
                        ),
                    ),
                    server_knowledge=44,
                )
            ],
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert len(prepared.updates) == 1
    assert prepared.updates[0].transaction_id == "txn-out"
    assert prepared.updates[0].new_memo == "Move money | [FX→] +/-12.34 HKD (rate: 7.8 HKD/USD)"


def test_build_prepared_conversion_keeps_zero_amount_transactions() -> None:
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
                amount_milliunits=0,
                memo="Adjustment",
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
                            amount_milliunits=0,
                            memo="Adjustment",
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

    assert len(prepared.updates) == 1
    assert prepared.updates[0].converted_amount_milliunits == 0
    assert prepared.updates[0].new_memo == "Adjustment | [FX] 0 HKD (rate: 7.8 HKD/USD)"
    assert prepared.skipped == ()


def _single_account_plan(divide_to_base: bool = True) -> PlanConfig:
    return PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={
            "HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=divide_to_base),
        },
    )


def test_resolve_bindings_raises_when_plan_not_found() -> None:
    gateway = FakeGateway(plans=(), account_snapshots={}, transaction_details={})
    with pytest.raises(ApiError, match="was not found"):
        resolve_bindings(_single_account_plan(), gateway)


def test_resolve_bindings_raises_when_multiple_plans_match() -> None:
    gateway = FakeGateway(
        plans=(
            RemotePlan(id="plan-1", name="Example Plan"),
            RemotePlan(id="plan-2", name="Example Plan"),
        ),
        account_snapshots={},
        transaction_details={},
    )
    with pytest.raises(ApiError, match="matched multiple YNAB plans"):
        resolve_bindings(_single_account_plan(), gateway)


def test_resolve_bindings_raises_when_account_missing() -> None:
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(accounts=(), server_knowledge=0),
        },
        transaction_details={},
    )
    with pytest.raises(ApiError, match="was not found"):
        resolve_bindings(_single_account_plan(), gateway)


def test_resolve_bindings_raises_when_account_matches_multiple() -> None:
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Travel HKD", deleted=False),
                ),
                server_knowledge=0,
            ),
        },
        transaction_details={},
    )
    with pytest.raises(ApiError, match="matched multiple YNAB accounts"):
        resolve_bindings(_single_account_plan(), gateway)


def test_resolve_bindings_skips_deleted_remote_accounts() -> None:
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=True),
                    RemoteAccount(id="acct-2", name="Travel HKD", deleted=False),
                ),
                server_knowledge=0,
            ),
        },
        transaction_details={},
    )
    bindings = resolve_bindings(_single_account_plan(), gateway)
    assert bindings.account_ids == {"travel_hkd": "acct-2"}


def test_build_prepared_conversion_raises_when_no_accounts_enabled() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=False),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    gateway = FakeGateway(plans=(), account_snapshots={}, transaction_details={})

    with pytest.raises(ConfigError, match="No enabled accounts"):
        build_prepared_conversion(
            plan=plan,
            state=AppState(version=1, plans={}),
            gateway=gateway,
            selected_account_aliases=(),
            bootstrap_since=None,
            prompt_for_start_date=lambda: date(2026, 4, 1),
        )


def test_build_prepared_conversion_raises_for_unknown_account_alias() -> None:
    gateway = FakeGateway(plans=(), account_snapshots={}, transaction_details={})
    with pytest.raises(UserInputError, match="Unknown or disabled"):
        build_prepared_conversion(
            plan=_single_account_plan(),
            state=AppState(version=1, plans={}),
            gateway=gateway,
            selected_account_aliases=("does_not_exist",),
            bootstrap_since=None,
            prompt_for_start_date=lambda: date(2026, 4, 1),
        )


def test_build_prepared_conversion_prompts_when_no_bootstrap_or_state() -> None:
    plan = _single_account_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            ),
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=50)],
        },
    )

    prompts: list[str] = []

    def _prompt() -> date:
        prompts.append("called")
        return date(2026, 4, 1)

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=None,
        prompt_for_start_date=_prompt,
    )

    assert prompts == ["called"]
    assert prepared.sync_request.used_bootstrap is True
    assert prepared.sync_request.since_date == date(2026, 4, 1)
    assert prepared.sync_request.last_knowledge_of_server is None


def test_build_prepared_conversion_skips_deleted_and_split_transactions() -> None:
    plan = _single_account_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            ),
        },
        transaction_details={
            "txn-split": RemoteTransactionDetail(
                id="txn-split",
                date=date(2026, 4, 12),
                amount_milliunits=5000,
                memo="Split",
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=2,
            ),
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-deleted",
                            date=date(2026, 4, 10),
                            amount_milliunits=1000,
                            memo=None,
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=True,
                        ),
                        RemoteTransaction(
                            id="txn-split",
                            date=date(2026, 4, 12),
                            amount_milliunits=5000,
                            memo="Split",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=42,
                ),
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    reasons = {skip.transaction_id: skip.reason for skip in prepared.skipped}
    assert reasons == {"txn-deleted": "deleted", "txn-split": "split"}
    assert prepared.updates == ()


def test_build_prepared_conversion_multiply_path_when_divide_false() -> None:
    plan = _single_account_plan(divide_to_base=False)
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            ),
        },
        transaction_details={
            "txn-1": RemoteTransactionDetail(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=1000,
                memo=None,
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
            ),
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=1000,
                            memo=None,
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=42,
                ),
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert len(prepared.updates) == 1
    assert prepared.updates[0].converted_amount_milliunits == 7800
    assert prepared.updates[0].pair_label == "USD/HKD"


def test_build_prepared_conversion_split_transfer_is_skipped_not_converted() -> None:
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
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Cash HKD", deleted=False),
                ),
                server_knowledge=1,
            ),
        },
        transaction_details={
            "txn-out": RemoteTransactionDetail(
                id="txn-out",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="Split transfer",
                account_id="acct-1",
                transfer_account_id="acct-2",
                transfer_transaction_id="txn-in",
                deleted=False,
                subtransaction_count=2,
            ),
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-out",
                            date=date(2026, 4, 10),
                            amount_milliunits=-12340,
                            memo="Split transfer",
                            account_id="acct-1",
                            transfer_account_id="acct-2",
                            transfer_transaction_id="txn-in",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                ),
            ],
            "acct-2": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-in",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Split transfer",
                            account_id="acct-2",
                            transfer_account_id="acct-1",
                            transfer_transaction_id="txn-out",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                ),
            ],
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert prepared.updates == ()
    reasons = {skip.transaction_id: skip.reason for skip in prepared.skipped}
    assert reasons == {"txn-out": "split", "txn-in": "paired-transfer"}


def test_build_prepared_conversion_keeps_single_side_of_transfer_to_unconfigured_account() -> None:
    plan = _single_account_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            ),
        },
        transaction_details={
            "txn-out": RemoteTransactionDetail(
                id="txn-out",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="Move money",
                account_id="acct-1",
                transfer_account_id="acct-unconfigured",
                transfer_transaction_id="txn-unseen",
                deleted=False,
                subtransaction_count=0,
            ),
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
                            transfer_account_id="acct-unconfigured",
                            transfer_transaction_id="txn-unseen",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                ),
            ],
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert len(prepared.updates) == 1
    assert prepared.updates[0].transaction_id == "txn-out"
    assert prepared.updates[0].is_transfer is True
    assert prepared.skipped == ()


def test_execute_conversion_dry_run_returns_without_writing() -> None:
    plan = _single_account_plan()
    gateway = FakeGateway(plans=(), account_snapshots={}, transaction_details={})
    prepared = PreparedConversion(
        bindings=ResolvedBindings(
            plan=plan,
            plan_id="plan-1",
            account_ids={"travel_hkd": "acct-1"},
        ),
        sync_request=SyncRequest(
            last_knowledge_of_server=10,
            since_date=None,
            used_bootstrap=False,
        ),
        queried_account_ids=("acct-1",),
        fetched_transactions=1,
        fetched_server_knowledge=10,
        updates=(
            _prepared_update(
                transaction_id="txn-1",
                account_alias="travel_hkd",
                memo="[FX] 1.00 HKD (rate: 7.8 HKD/USD)",
            ),
        ),
        skipped=(),
    )

    outcome = execute_conversion(
        prepared=prepared,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        apply_updates=False,
    )

    assert outcome.applied is False
    assert outcome.writes_performed == 0
    assert outcome.saved_server_knowledge is None
    assert gateway.updates == []
    assert gateway.update_batches == []


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


def _tracked_hkd_plan() -> PlanConfig:
    return PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(
                alias="travel_hkd",
                name="Travel HKD",
                currency="HKD",
                enabled=True,
                track_local_balance=True,
            ),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )


def test_build_prepared_conversion_populates_tracking_for_tracked_account() -> None:
    plan = _tracked_hkd_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(
                        id="acct-1",
                        name="Travel HKD",
                        deleted=False,
                        cleared_balance_milliunits=1582,
                    ),
                ),
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
                cleared="cleared",
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
                            cleared="cleared",
                        ),
                    ),
                    server_knowledge=44,
                )
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert len(prepared.tracking) == 1
    entry = prepared.tracking[0]
    assert entry.account_alias == "travel_hkd"
    assert entry.new_balance_milliunits == 12340
    assert entry.create_sentinel is not None
    assert entry.update_sentinel is None
    assert entry.within_tolerance is True
    assert prepared.rebuild_balance is False


def test_build_prepared_conversion_skips_sentinel_from_fx_pipeline() -> None:
    plan = _tracked_hkd_plan()
    sentinel_txn = RemoteTransaction(
        id="sentinel",
        date=date(2026, 4, 9),
        amount_milliunits=0,
        memo="[YMCA-BAL] HKD 0.00 | rate 7.8 HKD/USD | "
        "updated 2026-04-18T14:30:45Z | drift 0.00 USD",
        account_id="acct-1",
        transfer_account_id=None,
        transfer_transaction_id=None,
        deleted=False,
        payee_name="[YMCA] Tracked Balance",
        cleared="reconciled",
    )
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
                TransactionSnapshot(transactions=(sentinel_txn,), server_knowledge=44)
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )

    assert prepared.updates == ()
    assert any(skip.reason == "sentinel" for skip in prepared.skipped)
    assert len(prepared.tracking) == 1
    entry = prepared.tracking[0]
    assert entry.prior_sentinel is not None
    assert entry.update_sentinel is not None


def test_execute_conversion_writes_sentinel_creates_and_updates() -> None:
    plan = _tracked_hkd_plan()
    sentinel_existing = RemoteTransaction(
        id="existing-sentinel",
        date=date(2026, 4, 9),
        amount_milliunits=0,
        memo="[YMCA-BAL] HKD 1,000.00 | rate 7.8 HKD/USD | "
        "updated 2026-04-18T14:30:45Z | drift 0.00 USD",
        account_id="acct-1",
        transfer_account_id=None,
        transfer_transaction_id=None,
        deleted=False,
        payee_name="[YMCA] Tracked Balance",
        cleared="reconciled",
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(
                        id="acct-1",
                        name="Travel HKD",
                        deleted=False,
                        cleared_balance_milliunits=1582,
                    ),
                ),
                server_knowledge=1,
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
                cleared="cleared",
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        sentinel_existing,
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Dinner",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                            cleared="cleared",
                        ),
                    ),
                    server_knowledge=44,
                ),
                TransactionSnapshot(transactions=(), server_knowledge=55),
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )
    outcome = execute_conversion(
        prepared=prepared,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        apply_updates=True,
    )

    assert outcome.sentinel_writes == 1
    assert outcome.sentinels_created == 0
    # FX update + sentinel memo update → 2 single-transaction updates recorded.
    assert len(gateway.updates) == 2
    assert outcome.applied is True
    assert outcome.writes_performed == 1  # FX updates only; sentinel counted separately


def test_build_prepared_conversion_requires_tracked_account_for_rebuild_balance() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={"acct-1": [TransactionSnapshot((), 1)]},
    )

    with pytest.raises(
        UserInputError, match="--rebuild-balance requires at least one account"
    ):
        build_prepared_conversion(
            plan=plan,
            state=AppState(version=1, plans={}),
            gateway=gateway,
            selected_account_aliases=(),
            bootstrap_since=None,
            prompt_for_start_date=lambda: date(2026, 4, 1),
            rebuild_balance=True,
        )


def test_build_prepared_conversion_rebuild_balance_uses_full_scan() -> None:
    plan = _tracked_hkd_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(
                        id="acct-1",
                        name="Travel HKD",
                        deleted=False,
                        cleared_balance_milliunits=1582,
                    ),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-legacy",
                            date=date(2026, 4, 9),
                            amount_milliunits=1582,
                            memo="Dinner | 12.34 HKD (FX rate: 7.8)",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                            cleared="cleared",
                        ),
                    ),
                    server_knowledge=42,
                )
            ]
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(
            version=1,
            plans={
                "personal": PlanState(
                    plan_id="plan-1", account_ids={"travel_hkd": "acct-1"}, server_knowledge=7
                )
            },
        ),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=None,
        prompt_for_start_date=lambda: date(2026, 4, 1),
        rebuild_balance=True,
    )

    # sync_request ignores server_knowledge and uses no since_date
    assert prepared.sync_request.last_knowledge_of_server is None
    assert prepared.sync_request.since_date is None
    assert prepared.sync_request.used_bootstrap is True
    assert prepared.rebuild_balance is True
    assert prepared.tracking[0].new_balance_milliunits == 12340


def test_build_prepared_conversion_rebuild_balance_full_scans_only_tracked_accounts() -> None:
    plan = PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(
                alias="tracked_hkd",
                name="Tracked HKD",
                currency="HKD",
                enabled=True,
                track_local_balance=True,
            ),
            AccountConfig(
                alias="plain_gbp",
                name="Plain GBP",
                currency="GBP",
                enabled=True,
            ),
        ),
        fx_rates={
            "HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True),
            "GBP": FxRule(rate=Decimal("1.35"), rate_text="1.35", divide_to_base=False),
        },
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-hkd", name="Tracked HKD", deleted=False),
                    RemoteAccount(id="acct-gbp", name="Plain GBP", deleted=False),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-hkd": [TransactionSnapshot(transactions=(), server_knowledge=42)],
            "acct-gbp": [TransactionSnapshot(transactions=(), server_knowledge=43)],
        },
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"tracked_hkd": "acct-hkd", "plain_gbp": "acct-gbp"},
                server_knowledge=7,
            )
        },
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=None,
        prompt_for_start_date=lambda: date(2026, 4, 1),
        rebuild_balance=True,
    )

    assert prepared.rebuild_balance is True
    assert gateway.list_transactions_by_account_calls == [
        ("plan-1", "acct-hkd", None, None),
        ("plan-1", "acct-gbp", None, 7),
    ]


def test_build_prepared_conversion_fetches_saved_sentinel_when_delta_is_empty() -> None:
    """Regression: a quiet delta must not lose sight of the existing sentinel.

    Reproduces the user-reported bug where ``ymca sync`` on an up-to-date
    account printed ``sentinel: create`` + full drift despite the sentinel
    already existing in YNAB. The fix persists the sentinel id in state and
    fetches it directly via ``get_transaction_detail`` when the delta does
    not surface it.
    """
    plan = _tracked_hkd_plan()
    sentinel_memo = (
        "[YMCA-BAL] HKD 12,340.00 | rate 7.8 HKD/USD | "
        "updated 2026-04-18T12:00:00Z | drift 0.00 USD"
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(
                        id="acct-1",
                        name="Travel HKD",
                        deleted=False,
                        cleared_balance_milliunits=1582,
                    ),
                ),
                server_knowledge=42,
            )
        },
        transaction_details={
            "existing-sentinel": RemoteTransactionDetail(
                id="existing-sentinel",
                date=date(2026, 4, 18),
                amount_milliunits=0,
                memo=sentinel_memo,
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
                payee_name="[YMCA] Tracked Balance",
                cleared="reconciled",
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=42)]
        },
    )

    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=42,
                sentinel_ids={"travel_hkd": "existing-sentinel"},
            )
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

    assert len(prepared.tracking) == 1
    entry = prepared.tracking[0]
    assert entry.prior_sentinel is not None
    assert entry.prior_balance_milliunits == 12340000
    assert entry.new_balance_milliunits == 12340000
    assert entry.create_sentinel is None
    assert entry.update_sentinel is not None
    assert entry.update_sentinel.transaction_id == "existing-sentinel"


def test_build_prepared_conversion_reraises_saved_sentinel_lookup_errors() -> None:
    plan = _tracked_hkd_plan()

    class _SentinelLookupGateway(FakeGateway):
        def get_transaction_detail(
            self, plan_id: str, transaction_id: str
        ) -> RemoteTransactionDetail:
            del plan_id, transaction_id
            raise ApiError("Failed to get transaction detail via YNAB API. status=500")

    gateway = _SentinelLookupGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=42,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=42)]
        },
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=42,
                sentinel_ids={"travel_hkd": "existing-sentinel"},
            )
        },
    )

    with pytest.raises(ApiError, match="status=500"):
        build_prepared_conversion(
            plan=plan,
            state=state,
            gateway=gateway,
            selected_account_aliases=(),
            bootstrap_since=None,
            prompt_for_start_date=lambda: date(2026, 4, 1),
        )


def test_build_prepared_conversion_treats_404_saved_sentinel_as_missing() -> None:
    plan = _tracked_hkd_plan()

    class _MissingSentinelGateway(FakeGateway):
        def get_transaction_detail(
            self, plan_id: str, transaction_id: str
        ) -> RemoteTransactionDetail:
            del plan_id, transaction_id
            raise ApiError("Failed to get transaction detail via YNAB API. status=404")

    gateway = _MissingSentinelGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=42,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=42)]
        },
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=42,
                sentinel_ids={"travel_hkd": "missing-sentinel"},
            )
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

    entry = prepared.tracking[0]
    assert entry.prior_sentinel is None
    assert entry.create_sentinel is not None


def test_build_prepared_conversion_recreates_sentinel_when_user_deletes_it() -> None:
    """If the saved sentinel got deleted, we detect it and queue a new create."""
    plan = _tracked_hkd_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                ),
                server_knowledge=42,
            )
        },
        transaction_details={
            "ghost-sentinel": RemoteTransactionDetail(
                id="ghost-sentinel",
                date=date(2026, 4, 18),
                amount_milliunits=0,
                memo="stale",
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=True,  # user deleted this in YNAB
                subtransaction_count=0,
                payee_name="[YMCA] Tracked Balance",
                cleared="reconciled",
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [TransactionSnapshot(transactions=(), server_knowledge=42)]
        },
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=42,
                sentinel_ids={"travel_hkd": "ghost-sentinel"},
            )
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

    entry = prepared.tracking[0]
    assert entry.prior_sentinel is None
    assert entry.create_sentinel is not None
    assert entry.update_sentinel is None


def test_build_prepared_conversion_skips_saved_renamed_sentinel_from_fx_pipeline() -> None:
    plan = _tracked_hkd_plan()
    renamed_sentinel_memo = (
        "[YMCA-BAL] HKD 12,340.00 | rate 7.8 HKD/USD | "
        "updated 2026-04-18T12:00:00Z | drift 0.00 USD"
    )
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=42,
            )
        },
        transaction_details={
            "renamed-sentinel": RemoteTransactionDetail(
                id="renamed-sentinel",
                date=date(2026, 4, 18),
                amount_milliunits=0,
                memo=renamed_sentinel_memo,
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
                payee_name="Manually Renamed Sentinel",
                cleared="reconciled",
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="renamed-sentinel",
                            date=date(2026, 4, 18),
                            amount_milliunits=0,
                            memo=renamed_sentinel_memo,
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                            payee_name="Manually Renamed Sentinel",
                            cleared="reconciled",
                        ),
                    ),
                    server_knowledge=43,
                )
            ]
        },
    )
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"travel_hkd": "acct-1"},
                server_knowledge=42,
                sentinel_ids={"travel_hkd": "renamed-sentinel"},
            )
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

    assert prepared.updates == ()
    assert any(
        skip.transaction_id == "renamed-sentinel" and skip.reason == "sentinel"
        for skip in prepared.skipped
    )
    entry = prepared.tracking[0]
    assert entry.prior_sentinel is None
    assert entry.create_sentinel is not None
    assert entry.update_sentinel is None


def test_execute_conversion_persists_new_sentinel_ids_in_state() -> None:
    plan = _tracked_hkd_plan()
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(
                        id="acct-1",
                        name="Travel HKD",
                        deleted=False,
                        cleared_balance_milliunits=1582,
                    ),
                ),
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
                cleared="cleared",
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
                            cleared="cleared",
                        ),
                    ),
                    server_knowledge=44,
                ),
                TransactionSnapshot(transactions=(), server_knowledge=55),
            ]
        },
        create_transaction_ids=["freshly-created-sentinel"],
    )

    prepared = build_prepared_conversion(
        plan=plan,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=date(2026, 4, 1),
        prompt_for_start_date=lambda: date(2026, 4, 1),
    )
    outcome = execute_conversion(
        prepared=prepared,
        state=AppState(version=1, plans={}),
        gateway=gateway,
        apply_updates=True,
    )

    assert outcome.applied is True
    personal_state = outcome.new_state.plans["personal"]
    assert personal_state.sentinel_ids == {"travel_hkd": "freshly-created-sentinel"}
