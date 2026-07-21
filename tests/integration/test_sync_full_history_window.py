"""Live-API regression for edge case E32 (YNAB's 12-month default window).

YNAB's transactions endpoints silently limit results to roughly the trailing
12 months when ``since_date`` is omitted -- even when
``last_knowledge_of_server`` is supplied. A ``--rebuild-balance`` full scan
that relied on the bare endpoint would drop rows older than the window and
undercount the tracked balance.

This test seeds a cleared HKD row backdated ~14 months and asserts a rebuild
full scan still fetches, converts, and counts it, which only happens because
``_fetch_transactions_for_accounts`` sends the ``FULL_HISTORY_SINCE_DATE``
floor. The seed is deleted in a ``finally`` so the later session workflow's
own rebuild step never sees it.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ymca.conversion import build_prepared_conversion

from .conftest import IntegrationEnvironment
from .helpers import (
    apply_account_tracking,
    build_new_transaction,
    build_plan_config,
    empty_app_state,
    resolve_integration_accounts,
)

BACKDATED_PAYEE = "YMCA IT Backdated Beyond Window"
BACKDATED_AGE = timedelta(days=430)
BACKDATED_AMOUNT_MILLIUNITS = -78_000


def _prompt_never_called() -> date:
    pytest.fail("Full-scan rebuild must not reach the start-date prompt.")


@pytest.mark.integration
def test_rebuild_full_scan_fetches_rows_older_than_ynab_default_window(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = apply_account_tracking(
        build_plan_config(integration_env.plan.name, account_plan),
        {"hkd_main"},
    )
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id

    created = gateway.create_transactions(
        plan_id,
        [
            build_new_transaction(
                account_id=account_plan.hkd_primary.id,
                date_=date.today() - BACKDATED_AGE,
                amount_milliunits=BACKDATED_AMOUNT_MILLIUNITS,
                memo=None,
                payee_name=BACKDATED_PAYEE,
                cleared="cleared",
            )
        ],
    )
    assert len(created) == 1, "Backdated seed row was not created."
    seed_id = str(created[0].id)

    try:
        prepared = build_prepared_conversion(
            plan=plan_config,
            state=empty_app_state(),
            gateway=gateway,
            selected_account_aliases=("hkd_main",),
            bootstrap_since=None,
            prompt_for_start_date=_prompt_never_called,
            rebuild_balance=True,
        )

        update_ids = {update.transaction_id for update in prepared.updates}
        assert seed_id in update_ids, (
            "Rebuild full scan did not fetch the >12-month-old row; YNAB's "
            "default window is truncating the fetch (missing since_date floor)."
        )

        tracking_by_alias = {entry.account_alias: entry for entry in prepared.tracking}
        contributions = {
            contribution.transaction_id: contribution
            for contribution in tracking_by_alias["hkd_main"].contributions
        }
        assert seed_id in contributions, (
            "Backdated cleared row must contribute to the rebuilt tracked balance."
        )
        assert (
            contributions[seed_id].signed_source_milliunits
            == BACKDATED_AMOUNT_MILLIUNITS
        )
    finally:
        gateway.delete_transaction(plan_id, seed_id)
