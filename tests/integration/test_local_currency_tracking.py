"""Live-API coverage for workflows W11 and W12 (local currency tracking).

End-to-end verification of the sentinel-based local-currency balance against
the real YNAB API. A single test drives the full lifecycle to stay well under
the per-session call budget:

1. Seed a **cleared** HKD transaction and run ``ymca sync --apply`` with
   tracking enabled. The sentinel is created with the expected balance and the
   FX marker is appended to the seed's memo (W11, E26, E27).
2. Synthesize drift by hand-editing the sentinel memo, then run
   ``ymca sync --rebuild-balance --apply`` and assert the sentinel is
   reconstructed from the marked transactions (W12).
3. Soft-delete the seeded transaction, rerun the sync in delta mode, and
   assert the sentinel memo now reflects a zero balance (E23).

Estimated API cost: ~35 requests. Comfortably within the 150-call session
budget enforced by the integration harness.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest

from ymca.conversion import build_prepared_conversion, execute_conversion
from ymca.memo import SENTINEL_PAYEE_NAME
from ymca.models import AccountConfig, PlanConfig, TransactionUpdateRequest

from .conftest import IntegrationEnvironment
from .helpers import (
    build_new_transaction,
    build_plan_config,
    empty_app_state,
    find_transactions_by_payee_names,
    resolve_integration_accounts,
    transaction_ids_by_payee_name,
)

SEED_SINCE_DATE = date.today() - timedelta(days=1)


def _prompt_never_called() -> date:
    pytest.fail("Tracking integration test must not reach the bootstrap prompt.")


def _enable_tracking_on_hkd(plan_config: PlanConfig) -> PlanConfig:
    """Return a copy of ``plan_config`` with ``track_local_balance`` on HKD."""
    new_accounts: list[AccountConfig] = []
    for account in plan_config.accounts:
        if account.alias == "hkd_main":
            new_accounts.append(
                AccountConfig(
                    alias=account.alias,
                    name=account.name,
                    currency=account.currency,
                    enabled=account.enabled,
                    track_local_balance=True,
                )
            )
        else:
            new_accounts.append(account)
    return PlanConfig(
        alias=plan_config.alias,
        name=plan_config.name,
        base_currency=plan_config.base_currency,
        accounts=tuple(new_accounts),
        fx_rates=plan_config.fx_rates,
    )


def _find_sentinel(
    transactions: Sequence[Any], account_id: str
) -> list[Any]:
    return [
        transaction
        for transaction in transactions
        if getattr(transaction, "payee_name", None) == SENTINEL_PAYEE_NAME
        and not getattr(transaction, "deleted", False)
        and str(transaction.account_id) == account_id
    ]


@pytest.mark.integration
def test_local_currency_tracking_full_lifecycle(
    integration_env: IntegrationEnvironment,
) -> None:
    """Seed cleared → sync → verify → drift → rebuild → delete → delta-reversal."""
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = _enable_tracking_on_hkd(
        build_plan_config(integration_env.plan.name, account_plan)
    )
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id

    today = date.today()
    hkd_payee_name = "Integration tracking HKD"

    # 1. Seed a single *cleared* HKD transaction so the tracking branch engages.
    seed_transactions = [
        build_new_transaction(
            account_id=account_plan.hkd_primary.id,
            date_=today,
            amount_milliunits=-12340,
            memo="IT tracking hkd",
            payee_name=hkd_payee_name,
            cleared="cleared",
        ),
    ]
    gateway.create_transactions(plan_id, seed_transactions)

    raw_seeded = find_transactions_by_payee_names(
        gateway.list_plan_transactions_raw(plan_id),
        (hkd_payee_name,),
    )
    ids_by_payee = transaction_ids_by_payee_name(raw_seeded)
    seeded_id = ids_by_payee[hkd_payee_name]

    # 2. Run sync --apply. FX converts the seed and creates the sentinel.
    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=("hkd_main",),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )
    outcome = execute_conversion(
        prepared=prepared,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert outcome.applied is True
    assert len(prepared.tracking) == 1
    assert outcome.sentinel_writes == 1
    assert outcome.sentinels_created == 1
    assert prepared.tracking[0].new_balance_milliunits == -12340

    # 3. Confirm the sentinel landed in YNAB with the expected shape.
    raw_after_first = gateway.list_plan_transactions_raw(plan_id)
    sentinel_rows = _find_sentinel(raw_after_first, account_plan.hkd_primary.id)
    assert len(sentinel_rows) == 1
    sentinel = sentinel_rows[0]
    assert int(sentinel.amount) == 0
    assert "[YMCA-BAL] HKD -12.34" in (sentinel.memo or "")
    assert "rate 7.8 HKD/USD" in (sentinel.memo or "")
    sentinel_id = str(sentinel.id)
    sentinel_memo_after_first = sentinel.memo or ""

    # Confirm the seed was FX-converted (amount rewritten, memo appended).
    by_id_first = {str(txn.id): txn for txn in raw_after_first}
    seed_after_first = by_id_first[seeded_id]
    assert int(seed_after_first.amount) == -1582
    assert "[FX] -12.34 HKD (rate: 7.8 HKD/USD)" in (seed_after_first.memo or "")

    # 4. Hand-edit the sentinel memo to simulate drift, then trigger a rebuild.
    drifted_memo = (
        "[YMCA-BAL] HKD 9,999.99 | rate 7.8 HKD/USD | "
        "updated 2000-01-01T00:00:00Z | drift 0.00 USD"
    )
    gateway.update_transaction(
        plan_id,
        TransactionUpdateRequest(
            transaction_id=sentinel_id,
            amount_milliunits=0,
            memo=drifted_memo,
        ),
    )

    prepared_rebuild = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=("hkd_main",),
        bootstrap_since=None,
        prompt_for_start_date=_prompt_never_called,
        rebuild_balance=True,
    )
    assert prepared_rebuild.rebuild_balance is True
    assert prepared_rebuild.tracking
    rebuild_entry = prepared_rebuild.tracking[0]
    # Rebuild recomputes from the one marked cleared row (-12.34 HKD).
    assert rebuild_entry.new_balance_milliunits == -12340
    assert rebuild_entry.update_sentinel is not None
    assert rebuild_entry.update_sentinel.transaction_id == sentinel_id

    rebuild_outcome = execute_conversion(
        prepared=prepared_rebuild,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert rebuild_outcome.applied is True
    assert rebuild_outcome.sentinel_writes == 1
    assert rebuild_outcome.sentinels_created == 0

    raw_after_rebuild = gateway.list_plan_transactions_raw(plan_id)
    sentinel_after_rebuild = next(
        transaction
        for transaction in raw_after_rebuild
        if str(transaction.id) == sentinel_id
    )
    sentinel_memo_after_rebuild = sentinel_after_rebuild.memo or ""
    assert sentinel_memo_after_rebuild != drifted_memo
    assert "[YMCA-BAL] HKD -12.34" in sentinel_memo_after_rebuild

    # 5. Soft-delete the seeded transaction, rerun delta-mode sync, verify reversal.
    gateway.delete_transaction(plan_id, seeded_id)

    prepared_after_delete = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=("hkd_main",),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )
    assert prepared_after_delete.tracking
    entry_after_delete = prepared_after_delete.tracking[0]
    assert entry_after_delete.prior_sentinel is not None
    assert entry_after_delete.prior_balance_milliunits == -12340
    # Delta sees the marked row as cleared + deleted → subtract the contribution.
    assert any(
        contribution.reason == "delta-cleared-deleted"
        for contribution in entry_after_delete.contributions
    )
    assert entry_after_delete.new_balance_milliunits == 0
    assert entry_after_delete.update_sentinel is not None

    delete_outcome = execute_conversion(
        prepared=prepared_after_delete,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert delete_outcome.applied is True
    assert delete_outcome.sentinel_writes == 1
    assert delete_outcome.sentinels_created == 0

    raw_after_delete = gateway.list_plan_transactions_raw(plan_id)
    sentinel_final = next(
        transaction
        for transaction in raw_after_delete
        if str(transaction.id) == sentinel_id
    )
    final_memo = sentinel_final.memo or ""
    assert final_memo != sentinel_memo_after_first
    assert final_memo != sentinel_memo_after_rebuild
    assert "[YMCA-BAL] HKD 0.00" in final_memo
