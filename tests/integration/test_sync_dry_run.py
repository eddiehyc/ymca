"""Live-API coverage for workflow W4: ``ymca sync`` (dry run).

Seeds a rich dataset covering the edge cases documented in
``docs/edge-cases.md`` and asserts that :func:`build_prepared_conversion`
produces the expected ``PreparedUpdate`` / ``SkippedTransaction`` structure
without mutating any YNAB record.

The dry-run suite intentionally does NOT call :func:`execute_conversion`, so
no write traffic hits the test plan; the end-state of each seeded transaction
is verified to be identical to its seed state.

API cost (best case): 1 ``create_transaction`` + ``list_plans`` +
``list_accounts`` + 1 ``list_transactions_by_account`` per account +
detail fetches per convertable candidate == roughly 10 requests.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ymca.conversion import build_prepared_conversion
from ymca.memo import build_fx_marker

from .conftest import IntegrationEnvironment
from .helpers import (
    build_new_transaction,
    build_plan_config,
    build_sub_transaction,
    empty_app_state,
    find_transactions_by_payee_names,
    resolve_integration_accounts,
    transaction_ids_by_payee_name,
)

SEED_SINCE_DATE = date.today() - timedelta(days=1)


def _prompt_never_called() -> date:
    pytest.fail(
        "build_prepared_conversion requested a bootstrap date -- this test "
        "passes bootstrap_since so the prompt must not be reached."
    )


@pytest.mark.integration
def test_sync_dry_run_produces_expected_updates_and_skips(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = build_plan_config(integration_env.plan.name, account_plan)
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id

    hkd_account_id = account_plan.hkd_primary.id
    gbp_account_id = account_plan.gbp.id

    today = date.today()
    already_converted_memo = build_fx_marker(
        source_amount_milliunits=-1000,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )
    hkd_simple_payee = "Integration dry-run HKD"
    hkd_zero_payee = "Integration dry-run HKD zero"
    already_converted_payee = "Integration dry-run already-converted"
    legacy_payee = "Integration dry-run legacy"
    split_payee = "Integration dry-run split"
    gbp_payee = "Integration dry-run GBP"

    seed_transactions = [
        build_new_transaction(
            account_id=hkd_account_id,
            date_=today,
            amount_milliunits=-12340,
            memo="IT dryrun simple",
            payee_name=hkd_simple_payee,
        ),
        build_new_transaction(
            account_id=hkd_account_id,
            date_=today,
            amount_milliunits=0,
            memo="IT dryrun zero",
            payee_name=hkd_zero_payee,
        ),
        build_new_transaction(
            account_id=hkd_account_id,
            date_=today,
            amount_milliunits=-1000,
            memo=f"prior | {already_converted_memo}",
            payee_name=already_converted_payee,
        ),
        build_new_transaction(
            account_id=hkd_account_id,
            date_=today,
            amount_milliunits=-1000,
            memo="legacy 1.00 HKD (FX rate: 0.12821)",
            payee_name=legacy_payee,
        ),
        build_new_transaction(
            account_id=hkd_account_id,
            date_=today,
            amount_milliunits=-5000,
            memo="IT dryrun split parent",
            payee_name=split_payee,
            subtransactions=[
                build_sub_transaction(amount_milliunits=-3000, memo="IT split a"),
                build_sub_transaction(amount_milliunits=-2000, memo="IT split b"),
            ],
        ),
        build_new_transaction(
            account_id=gbp_account_id,
            date_=today,
            amount_milliunits=-1000,
            memo="IT dryrun gbp",
            payee_name=gbp_payee,
        ),
    ]
    gateway.create_transactions(plan_id, seed_transactions)
    raw_seeded = find_transactions_by_payee_names(
        gateway.list_plan_transactions_raw(plan_id),
        (
            hkd_simple_payee,
            hkd_zero_payee,
            already_converted_payee,
            legacy_payee,
            split_payee,
            gbp_payee,
        ),
    )
    ids_by_payee_name = transaction_ids_by_payee_name(raw_seeded)

    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )

    updates_by_txn = {update.transaction_id: update for update in prepared.updates}
    skip_reasons = {skipped.transaction_id: skipped.reason for skipped in prepared.skipped}

    hkd_simple_id = ids_by_payee_name[hkd_simple_payee]
    hkd_zero_id = ids_by_payee_name[hkd_zero_payee]
    already_converted_id = ids_by_payee_name[already_converted_payee]
    legacy_id = ids_by_payee_name[legacy_payee]
    split_id = ids_by_payee_name[split_payee]
    gbp_id = ids_by_payee_name[gbp_payee]

    assert hkd_simple_id in updates_by_txn, (
        "HKD simple transaction must produce a conversion update"
    )
    hkd_update = updates_by_txn[hkd_simple_id]
    assert hkd_update.source_amount_milliunits == -12340
    assert hkd_update.converted_amount_milliunits == -1582
    assert hkd_update.rate_text == "7.8"
    assert hkd_update.pair_label == "HKD/USD"
    assert "[FX]" in hkd_update.new_memo

    assert hkd_zero_id in updates_by_txn, "Zero-amount HKD transaction must still be processed (E1)"
    zero_update = updates_by_txn[hkd_zero_id]
    assert zero_update.source_amount_milliunits == 0
    assert zero_update.converted_amount_milliunits == 0
    assert "0 HKD" in zero_update.new_memo

    assert gbp_id in updates_by_txn, (
        "GBP multiply-path transaction must produce a conversion update"
    )
    gbp_update = updates_by_txn[gbp_id]
    assert gbp_update.source_amount_milliunits == -1000
    assert gbp_update.converted_amount_milliunits == -1350
    assert gbp_update.pair_label == "USD/GBP"

    assert skip_reasons.get(already_converted_id) == "already-converted"
    assert skip_reasons.get(legacy_id) == "legacy-marker"
    assert skip_reasons.get(split_id) == "split", (
        "Split transaction must be skipped with reason 'split' (E3). "
        f"Got skip map: {skip_reasons}"
    )

    assert prepared.fetched_transactions >= len(seed_transactions)

    raw_after = gateway.list_plan_transactions_raw(plan_id)
    ours_after = find_transactions_by_payee_names(
        raw_after,
        (
            hkd_simple_payee,
            hkd_zero_payee,
            already_converted_payee,
            legacy_payee,
            split_payee,
            gbp_payee,
        ),
    )
    assert len(ours_after) == len(seed_transactions), "Dry-run must not add or delete transactions"
    for transaction in ours_after:
        assert getattr(transaction, "payee_name", None) in ids_by_payee_name
        if transaction.id == hkd_simple_id:
            assert transaction.amount == -12340, "Dry-run must not change amounts"
            assert "[FX]" not in (transaction.memo or ""), "Dry-run must not append [FX] markers"
