"""Consolidated live-API workflow (W4–W7, W11–W12, E1–E3, E9) in one session.

The dedicated plan is emptied once per session in ``conftest.py``. This module
uses a **single** ordered scenario to limit YNAB traffic (200 req/hour cap):

1. Batch-seed rows covering dry-run skips (split, legacy, already-converted),
   uncleared vs cleared FX entry on a **tracked** HKD account, zero HKD, GBP.
2. ``build_prepared_conversion`` (bootstrap) — assert expected updates/skips.
3. ``execute_conversion`` — assert amounts, ``[FX]`` vs ``[FX+]``, sentinel.
4. Mark the uncleared HKD row cleared via the API; delta sync — assert
   ``[FX+]`` and sentinel includes both counted rows.
5. If ``HKD Integration 2`` exists: enable tracking on both HKD accounts,
   create a transfer (one leg cleared, one uncleared), sync twice — assert
   ``[FX→]`` then ``[FX+]`` and per-account sentinels.
6. Soft-delete a counted anchor row; bootstrap sync from empty state — assert
   sentinel reflects the reversal.
7. Hand-edit the sentinel memo; ``--rebuild-balance`` — assert correction.

Set ``YNAB_INTEGRATION_LEAVE_DIRTY=1`` to skip session teardown so the plan
stays visible in the YNAB UI (next run still wipes at session start).

**Never** commit a YNAB API key; use env ``YNAB_API_KEY`` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

import pytest

from ymca.conversion import build_prepared_conversion, execute_conversion
from ymca.memo import SENTINEL_PAYEE_NAME, build_fx_marker
from ymca.models import TransactionUpdateRequest

from .conftest import IntegrationEnvironment
from .helpers import (
    apply_account_tracking,
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
    pytest.fail("Integration session workflow passes bootstrap_since / rebuild flags explicitly.")


def _one_raw_by_payee(raw: Sequence[Any], payee: str) -> Any:
    matches = [
        row
        for row in raw
        if getattr(row, "payee_name", None) == payee and not getattr(row, "deleted", False)
    ]
    assert len(matches) == 1, (payee, [getattr(r, "payee_name", None) for r in raw])
    return matches[0]


def _find_sentinel(transactions: Sequence[Any], account_id: str) -> list[Any]:
    return [
        row
        for row in transactions
        if getattr(row, "payee_name", None) == SENTINEL_PAYEE_NAME
        and not getattr(row, "deleted", False)
        and str(row.account_id) == account_id
    ]


def _find_transfer_legs(
    raw: Sequence[Any], *, account_out: str, account_in: str
) -> tuple[Any, Any]:
    active = [t for t in raw if not getattr(t, "deleted", False)]
    by_id = {str(t.id): t for t in active}
    for row in active:
        other_id = getattr(row, "transfer_transaction_id", None)
        if other_id is None:
            continue
        other = by_id.get(str(other_id))
        if other is None:
            continue
        pair_accounts = {str(row.account_id), str(other.account_id)}
        if pair_accounts != {account_out, account_in}:
            continue
        if str(row.account_id) == account_out:
            return row, other
        return other, row
    raise AssertionError(
        f"No transfer pair between accounts {account_out} and {account_in}."
    )


@pytest.mark.integration
def test_integration_session_all_workflows(integration_env: IntegrationEnvironment) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = apply_account_tracking(
        build_plan_config(integration_env.plan.name, account_plan),
        {"hkd_main"},
    )
    gateway = integration_env.gateway
    plan_id = integration_env.plan.id
    today = date.today()

    payee_uc = "IT session HKD uncleared"
    payee_cl = "IT session HKD cleared anchor"
    payee_zero = "IT session HKD zero"
    payee_gbp = "IT session GBP"
    payee_split = "IT session split"
    payee_legacy = "IT session legacy"
    already_marker = build_fx_marker(
        source_amount_milliunits=-1000,
        source_currency="HKD",
        rate_text="7.8",
        pair_label="HKD/USD",
    )
    payee_already = "IT session already fx"

    hkd_id = account_plan.hkd_primary.id
    gbp_id = account_plan.gbp.id

    seed = [
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=-12340,
            memo="session uncleared",
            payee_name=payee_uc,
            cleared="uncleared",
        ),
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=-5000,
            memo="session cleared anchor",
            payee_name=payee_cl,
            cleared="cleared",
        ),
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=0,
            memo="session zero",
            payee_name=payee_zero,
            cleared="uncleared",
        ),
        build_new_transaction(
            account_id=gbp_id,
            date_=today,
            amount_milliunits=-1000,
            memo="session gbp",
            payee_name=payee_gbp,
            cleared="uncleared",
        ),
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=-5000,
            memo="session split parent",
            payee_name=payee_split,
            cleared="uncleared",
            subtransactions=[
                build_sub_transaction(amount_milliunits=-3000, memo="split a"),
                build_sub_transaction(amount_milliunits=-2000, memo="split b"),
            ],
        ),
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=-1000,
            memo="legacy 1.00 HKD (FX rate: 0.12821)",
            payee_name=payee_legacy,
            cleared="uncleared",
        ),
        build_new_transaction(
            account_id=hkd_id,
            date_=today,
            amount_milliunits=-1000,
            memo=f"prior | {already_marker}",
            payee_name=payee_already,
            cleared="uncleared",
        ),
    ]
    gateway.create_transactions(plan_id, seed)
    raw_seeded = find_transactions_by_payee_names(
        gateway.list_plan_transactions_raw(plan_id),
        (
            payee_uc,
            payee_cl,
            payee_zero,
            payee_gbp,
            payee_split,
            payee_legacy,
            payee_already,
        ),
    )
    ids_by_payee = transaction_ids_by_payee_name(raw_seeded)

    prepared_first = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=SEED_SINCE_DATE,
        prompt_for_start_date=_prompt_never_called,
    )
    updates_by_id = {u.transaction_id: u for u in prepared_first.updates}
    skip_reasons = {s.transaction_id: s.reason for s in prepared_first.skipped}

    assert ids_by_payee[payee_split] in skip_reasons
    assert skip_reasons[ids_by_payee[payee_split]] == "split"
    assert ids_by_payee[payee_legacy] in skip_reasons
    assert skip_reasons[ids_by_payee[payee_legacy]] == "legacy-marker"
    assert ids_by_payee[payee_already] in skip_reasons
    assert skip_reasons[ids_by_payee[payee_already]] == "already-converted"

    for payee in (payee_uc, payee_cl, payee_zero, payee_gbp):
        assert ids_by_payee[payee] in updates_by_id

    outcome_first = execute_conversion(
        prepared=prepared_first,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert outcome_first.applied is True
    assert outcome_first.sentinels_created == 1
    memo_flip_count = sum(len(t.memo_flips) for t in prepared_first.tracking)
    assert outcome_first.sentinel_writes == memo_flip_count + outcome_first.sentinels_created

    raw_after_first = gateway.list_plan_transactions_raw(plan_id)
    row_uc = _one_raw_by_payee(raw_after_first, payee_uc)
    row_cl = _one_raw_by_payee(raw_after_first, payee_cl)
    assert int(row_uc.amount) == -1582
    assert "[FX] -12.34 HKD (rate: 7.8 HKD/USD)" in (row_uc.memo or "")
    assert "[FX+]" not in (row_uc.memo or "")
    assert int(row_cl.amount) == -641
    assert "[FX+]" in (row_cl.memo or "")
    assert "5 HKD" in (row_cl.memo or "") and "rate: 7.8 HKD/USD" in (row_cl.memo or "")

    row_zero = _one_raw_by_payee(raw_after_first, payee_zero)
    assert int(row_zero.amount) == 0
    assert "[FX] 0 HKD" in (row_zero.memo or "")

    row_gbp = _one_raw_by_payee(raw_after_first, payee_gbp)
    assert int(row_gbp.amount) == -1350
    assert "[FX] -1 GBP (rate: 1.35 USD/GBP)" in (row_gbp.memo or "")

    sentinels = _find_sentinel(raw_after_first, hkd_id)
    assert len(sentinels) == 1
    sentinel_id = str(sentinels[0].id)
    assert int(sentinels[0].amount) == 0
    assert "-5.00 HKD [YMCA-BAL]" in (sentinels[0].memo or "")

    # --- uncleared → cleared (delta) ---
    row_uc = _one_raw_by_payee(raw_after_first, payee_uc)
    gateway.update_transaction(
        plan_id,
        TransactionUpdateRequest(
            transaction_id=str(row_uc.id),
            amount_milliunits=int(row_uc.amount),
            memo=row_uc.memo or "",
            cleared="cleared",
        ),
    )

    prepared_delta = build_prepared_conversion(
        plan=plan_config,
        state=outcome_first.new_state,
        gateway=gateway,
        selected_account_aliases=(),
        bootstrap_since=None,
        prompt_for_start_date=_prompt_never_called,
    )
    outcome_delta = execute_conversion(
        prepared=prepared_delta,
        state=outcome_first.new_state,
        gateway=gateway,
        apply_updates=True,
    )
    assert outcome_delta.applied is True

    raw_after_delta = gateway.list_plan_transactions_raw(plan_id)
    row_uc2 = _one_raw_by_payee(raw_after_delta, payee_uc)
    assert "[FX+] -12.34 HKD (rate: 7.8 HKD/USD)" in (row_uc2.memo or "")
    sentinels2 = _find_sentinel(raw_after_delta, hkd_id)
    assert len(sentinels2) == 1
    assert "-17.34 HKD [YMCA-BAL]" in (sentinels2[0].memo or "")

    # --- soft-delete a counted row; delta sync sees the tombstone (E23) ---
    state_before_delete = outcome_delta.new_state
    gateway.delete_transaction(plan_id, ids_by_payee[payee_cl])

    prepared_del = build_prepared_conversion(
        plan=plan_config,
        state=state_before_delete,
        gateway=gateway,
        selected_account_aliases=("hkd_main",),
        bootstrap_since=None,
        prompt_for_start_date=_prompt_never_called,
    )
    assert any(c.reason == "uncount" for c in prepared_del.tracking[0].contributions)
    outcome_del = execute_conversion(
        prepared=prepared_del,
        state=state_before_delete,
        gateway=gateway,
        apply_updates=True,
    )
    assert outcome_del.applied is True
    raw_del = gateway.list_plan_transactions_raw(plan_id)
    sentinels_del = _find_sentinel(raw_del, hkd_id)
    assert len(sentinels_del) == 1
    assert "-12.34 HKD [YMCA-BAL]" in (sentinels_del[0].memo or "")

    # --- rebuild after hand-edited sentinel (W12) ---
    drifted = "9,999.99 HKD [YMCA-BAL]"
    gateway.update_transaction(
        plan_id,
        TransactionUpdateRequest(
            transaction_id=sentinel_id,
            amount_milliunits=0,
            memo=drifted,
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
    out_rebuild = execute_conversion(
        prepared=prepared_rebuild,
        state=empty_app_state(),
        gateway=gateway,
        apply_updates=True,
    )
    assert out_rebuild.applied is True
    raw_after_rebuild = gateway.list_plan_transactions_raw(plan_id)
    sentinel_after_rebuild = next(t for t in raw_after_rebuild if str(t.id) == sentinel_id)
    assert drifted not in (sentinel_after_rebuild.memo or "")
    assert "-12.34 HKD [YMCA-BAL]" in (sentinel_after_rebuild.memo or "")

    # --- transfer + partial clear (E2 pairing + directional markers), last ---
    if account_plan.hkd_secondary is not None:
        plan_both_tracked = apply_account_tracking(
            build_plan_config(integration_env.plan.name, account_plan),
            {"hkd_main", "hkd_secondary"},
        )
        transfer_payee_id = gateway.get_transfer_payee_id(plan_id, account_plan.hkd_secondary.id)
        sec_id = account_plan.hkd_secondary.id
        if transfer_payee_id is not None:
            gateway.create_transactions(
                plan_id,
                [
                    build_new_transaction(
                        account_id=hkd_id,
                        date_=today,
                        amount_milliunits=-20000,
                        memo="session xfer",
                        payee_id=transfer_payee_id,
                        cleared="cleared",
                    ),
                ],
            )
            raw_pre = gateway.list_plan_transactions_raw(plan_id)
            xfer_out_pre, xfer_in_pre = _find_transfer_legs(
                raw_pre, account_out=hkd_id, account_in=sec_id
            )
            assert int(xfer_out_pre.amount) == -20000
            assert int(xfer_in_pre.amount) == 20000

            prepared_xfer1 = build_prepared_conversion(
                plan=plan_both_tracked,
                state=out_rebuild.new_state,
                gateway=gateway,
                selected_account_aliases=(),
                bootstrap_since=None,
                prompt_for_start_date=_prompt_never_called,
            )
            transfer_updates = [u for u in prepared_xfer1.updates if u.is_transfer]
            transfer_skips = [s for s in prepared_xfer1.skipped if s.reason == "paired-transfer"]
            assert len(transfer_updates) == 1
            assert len(transfer_skips) >= 1

            outcome_xfer1 = execute_conversion(
                prepared=prepared_xfer1,
                state=out_rebuild.new_state,
                gateway=gateway,
                apply_updates=True,
            )
            assert outcome_xfer1.applied is True

            raw_x1 = gateway.list_plan_transactions_raw(plan_id)
            out_id, in_id = str(xfer_out_pre.id), str(xfer_in_pre.id)
            xfer_out_1 = next(t for t in raw_x1 if str(t.id) == out_id)
            xfer_in_1 = next(t for t in raw_x1 if str(t.id) == in_id)
            assert (xfer_out_1.memo or "") == (xfer_in_1.memo or "")
            assert "[FX→]" in (xfer_out_1.memo or "")

            main_sent_1 = _find_sentinel(raw_x1, hkd_id)
            sec_sent_1 = _find_sentinel(raw_x1, sec_id)
            assert len(main_sent_1) == 1 and len(sec_sent_1) == 1
            assert "-32.34 HKD [YMCA-BAL]" in (main_sent_1[0].memo or "")
            assert "0.00 HKD [YMCA-BAL]" in (sec_sent_1[0].memo or "")

            gateway.update_transaction(
                plan_id,
                TransactionUpdateRequest(
                    transaction_id=in_id,
                    amount_milliunits=int(xfer_in_1.amount),
                    memo=xfer_in_1.memo or "",
                    cleared="cleared",
                ),
            )

            prepared_xfer2 = build_prepared_conversion(
                plan=plan_both_tracked,
                state=outcome_xfer1.new_state,
                gateway=gateway,
                selected_account_aliases=(),
                bootstrap_since=None,
                prompt_for_start_date=_prompt_never_called,
            )
            outcome_xfer2 = execute_conversion(
                prepared=prepared_xfer2,
                state=outcome_xfer1.new_state,
                gateway=gateway,
                apply_updates=True,
            )
            assert outcome_xfer2.applied is True
            raw_x2 = gateway.list_plan_transactions_raw(plan_id)
            xfer_out_2 = next(t for t in raw_x2 if str(t.id) == out_id)
            xfer_in_2 = next(t for t in raw_x2 if str(t.id) == in_id)
            assert "[FX+]" in (xfer_out_2.memo or "")
            assert "[FX+]" in (xfer_in_2.memo or "")

            main_sent_2 = _find_sentinel(raw_x2, hkd_id)
            sec_sent_2 = _find_sentinel(raw_x2, sec_id)
            assert "-32.34 HKD [YMCA-BAL]" in (main_sent_2[0].memo or "")
            assert "20.00 HKD [YMCA-BAL]" in (sec_sent_2[0].memo or "")
