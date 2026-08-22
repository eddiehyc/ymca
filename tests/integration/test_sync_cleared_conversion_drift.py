"""Live-API regression for edge case E34 (drift baseline vs pending conversions).

YNAB reports ``cleared_balance`` from before a sync's FX writes land, so a row
the user entered as *cleared* is still counted there at its raw source-currency
amount. Comparing the tracked balance against that figure reported the entire
FX spread as drift and told the user to run ``--rebuild-balance`` for a
perfectly healthy account.

The tolerance check therefore rebases onto the balance YNAB will report *after*
this run's writes. This test drives the sequence against the real API:

1. Seed and convert a cleared 780 HKD row so the account holds a tracked
   balance and a sentinel (100.00 USD in YNAB, 780.00 HKD tracked).
2. Enter a second cleared 78 HKD row, exactly as a user would in the YNAB UI.
3. Assert the next sync prepares the conversion **and** stays within tolerance.

It also asserts the raw, unadjusted comparison would have failed, so the test
still fails if the projection silently stops being applied.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ymca.balance import TOLERANCE_STRONGER_MILLIUNITS, compute_drift_milliunits_stronger
from ymca.conversion import build_prepared_conversion, execute_conversion

from .conftest import IntegrationEnvironment
from .helpers import (
    apply_account_tracking,
    build_new_transaction,
    build_plan_config,
    clear_active_plan_transactions,
    empty_app_state,
    resolve_integration_accounts,
)

SEED_PAYEE = "YMCA IT Drift Seed"
NEW_CLEARED_PAYEE = "YMCA IT Drift New Cleared"
SEED_AMOUNT_MILLIUNITS = 780_000
NEW_AMOUNT_MILLIUNITS = 78_000
EXPECTED_TRACKED_MILLIUNITS = SEED_AMOUNT_MILLIUNITS + NEW_AMOUNT_MILLIUNITS


def _prompt_never_called() -> date:
    pytest.fail("Drift regression must not reach the start-date prompt.")


@pytest.mark.integration
def test_new_cleared_row_does_not_report_drift_before_its_conversion(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = apply_account_tracking(
        build_plan_config(integration_env.plan.name, account_plan),
        {"hkd_main"},
    )
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id
    account_id = account_plan.hkd_primary.id
    bootstrap_since = date.today() - timedelta(days=1)

    # Start from a clean account so ``cleared_balance`` only reflects this test.
    clear_active_plan_transactions(gateway, plan_id)

    try:
        created = gateway.create_transactions(
            plan_id,
            [
                build_new_transaction(
                    account_id=account_id,
                    date_=date.today(),
                    amount_milliunits=SEED_AMOUNT_MILLIUNITS,
                    memo=None,
                    payee_name=SEED_PAYEE,
                    cleared="cleared",
                )
            ],
        )
        assert len(created) == 1, "Seed row was not created."

        seed_prepared = build_prepared_conversion(
            plan=plan_config,
            state=empty_app_state(),
            gateway=gateway,
            selected_account_aliases=("hkd_main",),
            bootstrap_since=bootstrap_since,
            prompt_for_start_date=_prompt_never_called,
        )
        seed_outcome = execute_conversion(
            prepared=seed_prepared,
            state=empty_app_state(),
            gateway=gateway,
            apply_updates=True,
        )
        assert seed_outcome.applied is True

        # The user enters a new row and marks it cleared in the YNAB UI.
        gateway.create_transactions(
            plan_id,
            [
                build_new_transaction(
                    account_id=account_id,
                    date_=date.today(),
                    amount_milliunits=NEW_AMOUNT_MILLIUNITS,
                    memo=None,
                    payee_name=NEW_CLEARED_PAYEE,
                    cleared="cleared",
                )
            ],
        )

        prepared = build_prepared_conversion(
            plan=plan_config,
            state=seed_outcome.new_state,
            gateway=gateway,
            selected_account_aliases=("hkd_main",),
            bootstrap_since=None,
            prompt_for_start_date=_prompt_never_called,
        )

        pending_source_amounts = [
            update.source_amount_milliunits for update in prepared.updates
        ]
        assert pending_source_amounts == [NEW_AMOUNT_MILLIUNITS], (
            "The newly cleared row should be the only pending FX conversion."
        )

        entry = next(
            item for item in prepared.tracking if item.account_alias == "hkd_main"
        )
        assert entry.new_balance_milliunits == EXPECTED_TRACKED_MILLIUNITS

        raw_drift = compute_drift_milliunits_stronger(
            tracked_source_milliunits=entry.new_balance_milliunits,
            ynab_cleared_balance_base_milliunits=entry.ynab_cleared_balance_milliunits,
            rule=plan_config.fx_rates["HKD"],
        )
        assert abs(raw_drift) > TOLERANCE_STRONGER_MILLIUNITS, (
            "Live YNAB should still report the pre-conversion cleared balance; "
            "without that precondition this test proves nothing."
        )

        assert entry.pending_conversion_delta_milliunits != 0
        assert entry.within_tolerance is True, (
            "A cleared row awaiting its first FX conversion must not be "
            f"reported as drift (drift={entry.drift_milliunits_stronger} "
            f"{entry.stronger_currency})."
        )
    finally:
        clear_active_plan_transactions(gateway, plan_id)
