"""Live-API coverage for workflow W5: ``ymca convert --apply``.

Runs the full convert pipeline against a small seeded dataset, persisting
amount and memo changes to YNAB, and then re-reads the plan to verify that
the mutations landed exactly as the in-memory :class:`PreparedConversion`
predicted.

Edge cases asserted directly here:

* :ref:`E1 <edge-cases>`: zero-amount HKD transaction round-trips to
  amount ``0`` with a memo-only rewrite.
* :ref:`E8 <edge-cases>`: milliunit rounding of the HKD divide path.
* :ref:`E9 <edge-cases>`: GBP multiply path with the correct pair label.
* :ref:`E2 <edge-cases>`: transfer pair is processed on exactly one leg.

API cost (best case): ~13 requests -- within budget for a re-runnable suite.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ymca.conversion import build_prepared_conversion, execute_conversion

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
    pytest.fail("convert --apply should not prompt when bootstrap_since is given.")


@pytest.mark.integration
def test_convert_apply_persists_amount_and_memo_changes(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = build_plan_config(integration_env.plan.name, account_plan)
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id

    today = date.today()
    hkd_payee_name = "Integration apply HKD"
    zero_payee_name = "Integration apply zero"
    gbp_payee_name = "Integration apply GBP"

    seed_transactions = [
        build_new_transaction(
            account_id=account_plan.hkd_primary.id,
            date_=today,
            amount_milliunits=-12340,
            memo="IT apply hkd",
            payee_name=hkd_payee_name,
        ),
        build_new_transaction(
            account_id=account_plan.hkd_primary.id,
            date_=today,
            amount_milliunits=0,
            memo="IT apply zero",
            payee_name=zero_payee_name,
        ),
        build_new_transaction(
            account_id=account_plan.gbp.id,
            date_=today,
            amount_milliunits=-1000,
            memo="IT apply gbp",
            payee_name=gbp_payee_name,
        ),
    ]
    gateway.create_transactions(plan_id, seed_transactions)
    raw_seeded = find_transactions_by_payee_names(
        gateway.list_plan_transactions_raw(plan_id),
        (hkd_payee_name, zero_payee_name, gbp_payee_name),
    )
    id_by_payee_name = transaction_ids_by_payee_name(raw_seeded)

    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )
    assert prepared.updates, "Expected at least one update in the apply seed."

    outcome = execute_conversion(
        prepared=prepared,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert outcome.applied is True
    assert outcome.writes_performed == len(prepared.updates)
    assert outcome.saved_server_knowledge is not None

    raw_after = gateway.list_plan_transactions_raw(plan_id)
    by_id = {str(transaction.id): transaction for transaction in raw_after}

    hkd_after = by_id[id_by_payee_name[hkd_payee_name]]
    assert hkd_after.amount == -1582, "HKD divide path must round 12340/7.8 → 1582 milliunits"
    assert "[FX] -12.34 HKD (rate: 7.8 HKD/USD)" in (hkd_after.memo or "")

    zero_after = by_id[id_by_payee_name[zero_payee_name]]
    assert zero_after.amount == 0, "Zero-amount transaction must remain zero after apply (E1)"
    assert "[FX] 0 HKD" in (zero_after.memo or "")

    gbp_after = by_id[id_by_payee_name[gbp_payee_name]]
    assert gbp_after.amount == -1350, "GBP multiply path: 1000 * 1.35 → 1350 milliunits (E9)"
    assert "[FX] -1 GBP (rate: 1.35 USD/GBP)" in (gbp_after.memo or "")

    seeded_after = find_transactions_by_payee_names(
        raw_after,
        (hkd_payee_name, zero_payee_name, gbp_payee_name),
    )
    assert len(seeded_after) == len(seed_transactions), (
        "Apply must not delete seeded transactions -- harness cleanup happens "
        "only at session teardown."
    )


@pytest.mark.integration
def test_convert_apply_handles_transfer_pair_once(
    integration_env: IntegrationEnvironment,
) -> None:
    """Exercise edge case E2 (transfer pair) if a second HKD account exists."""
    account_plan = resolve_integration_accounts(integration_env.accounts)
    if account_plan.hkd_secondary is None:
        pytest.skip(
            "Transfer-pair integration expected the harness-provisioned "
            "'HKD Integration 2' account, but it was not available."
        )

    plan_config = build_plan_config(integration_env.plan.name, account_plan)
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id

    transfer_payee_id = gateway.get_transfer_payee_id(
        plan_id, account_plan.hkd_secondary.id
    )
    if transfer_payee_id is None:
        pytest.skip("No transfer payee for the secondary HKD account; skipping.")

    today = date.today()

    seed = [
        build_new_transaction(
            account_id=account_plan.hkd_primary.id,
            date_=today,
            amount_milliunits=-50000,
            memo="IT transfer pair",
            payee_id=transfer_payee_id,
        ),
    ]
    gateway.create_transactions(plan_id, seed)

    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )

    transfer_updates = [update for update in prepared.updates if update.is_transfer]
    transfer_skips = [skip for skip in prepared.skipped if skip.reason == "paired-transfer"]
    assert len(transfer_updates) == 1, (
        "Transfer pair must produce exactly one update after dedup (E2). "
        f"Updates: {[u.transaction_id for u in transfer_updates]}"
    )
    assert len(transfer_skips) >= 1, (
        "Transfer pair must skip the mirrored side with reason 'paired-transfer' (E2). "
        f"Skips: {[(s.transaction_id, s.reason) for s in prepared.skipped]}"
    )
