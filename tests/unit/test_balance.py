"""Unit tests for :mod:`ymca.balance`.

Covers the tracking algorithm's transition matrix (§12.4), the rebuild-mode
parser (§12.5), the drift math (§12.6), and every documented edge case from
E21 through E28.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from ymca.balance import (
    TOLERANCE_STRONGER_MILLIUNITS,
    build_tracking_update,
    compute_drift_milliunits_stronger,
    find_existing_sentinel,
    snapshot_sentinel,
    within_tolerance,
)
from ymca.memo import SENTINEL_FLAG_COLOR, SENTINEL_PAYEE_NAME, build_sentinel_memo
from ymca.models import (
    AccountConfig,
    FxRule,
    PlanConfig,
    RemoteAccount,
    RemoteTransaction,
)

_NOW = datetime(2026, 4, 19, 14, 30, 45, tzinfo=UTC)
_PRIOR = datetime(2026, 4, 18, 14, 30, 45, tzinfo=UTC)


def _plan() -> PlanConfig:
    return PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(
                alias="hkd_wallet",
                name="HKD Wallet",
                currency="HKD",
                enabled=True,
                track_local_balance=True,
            ),
            AccountConfig(
                alias="gbp_wallet",
                name="GBP Wallet",
                currency="GBP",
                enabled=True,
                track_local_balance=True,
            ),
        ),
        fx_rates={
            "HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True),
            "GBP": FxRule(rate=Decimal("1.35"), rate_text="1.35", divide_to_base=False),
        },
    )


def _hkd_account(cleared_balance_milliunits: int = 0) -> RemoteAccount:
    return RemoteAccount(
        id="acct-hkd",
        name="HKD Wallet",
        deleted=False,
        closed=False,
        cleared_balance_milliunits=cleared_balance_milliunits,
    )


def _gbp_account(cleared_balance_milliunits: int = 0) -> RemoteAccount:
    return RemoteAccount(
        id="acct-gbp",
        name="GBP Wallet",
        deleted=False,
        closed=False,
        cleared_balance_milliunits=cleared_balance_milliunits,
    )


def _txn(
    *,
    txn_id: str = "txn",
    amount_milliunits: int = 0,
    memo: str | None = None,
    cleared: str = "uncleared",
    deleted: bool = False,
    transfer_id: str | None = None,
    payee_name: str | None = None,
    paired_transfer_counted: bool | None = None,
) -> RemoteTransaction:
    return RemoteTransaction(
        id=txn_id,
        date=date(2026, 4, 10),
        amount_milliunits=amount_milliunits,
        memo=memo,
        account_id="acct-hkd",
        transfer_account_id="acct-2" if transfer_id is not None else None,
        transfer_transaction_id=transfer_id,
        deleted=deleted,
        payee_name=payee_name,
        cleared=cleared,  # type: ignore[arg-type]
        paired_transfer_counted=paired_transfer_counted,
    )


def test_within_tolerance_and_drift_math_divide_to_base_hkd() -> None:
    # HKD/USD with rate 7.8 (divide_to_base=true). USD is the stronger currency.
    # Tracked 7.8 HKD back-converts to 1.00 USD. Cleared balance = 1.00 USD.
    rule = _plan().fx_rates["HKD"]
    drift = compute_drift_milliunits_stronger(
        tracked_source_milliunits=7800,
        ynab_cleared_balance_base_milliunits=1000,
        rule=rule,
    )
    assert drift == 0
    assert within_tolerance(drift) is True
    assert within_tolerance(TOLERANCE_STRONGER_MILLIUNITS)
    assert within_tolerance(-TOLERANCE_STRONGER_MILLIUNITS)
    assert not within_tolerance(TOLERANCE_STRONGER_MILLIUNITS + 1)


def test_drift_math_multiply_to_base_gbp_uses_source_as_stronger() -> None:
    # GBP/USD with rate 1.35 (divide_to_base=false). GBP is the stronger currency.
    # Tracked 100 GBP; cleared balance 135 USD → 100 GBP (via /1.35 → 100).
    rule = _plan().fx_rates["GBP"]
    drift = compute_drift_milliunits_stronger(
        tracked_source_milliunits=100000,
        ynab_cleared_balance_base_milliunits=135000,
        rule=rule,
    )
    assert drift == 0


def test_drift_math_reports_signed_delta_in_stronger_currency() -> None:
    rule = _plan().fx_rates["HKD"]
    # Tracked 7.8 HKD, cleared 0 USD. Drift = 1.00 USD = 1000 milliunits.
    drift = compute_drift_milliunits_stronger(
        tracked_source_milliunits=7800,
        ynab_cleared_balance_base_milliunits=0,
        rule=rule,
    )
    assert drift == 1000


def test_find_existing_sentinel_matches_by_payee_and_skips_deleted() -> None:
    regular = _txn(txn_id="t1", amount_milliunits=1000)
    sentinel_deleted = _txn(
        txn_id="t-del",
        amount_milliunits=0,
        deleted=True,
        payee_name=SENTINEL_PAYEE_NAME,
    )
    sentinel_live = _txn(
        txn_id="t-keep", amount_milliunits=0, payee_name=SENTINEL_PAYEE_NAME
    )

    assert find_existing_sentinel([regular, sentinel_deleted, sentinel_live]) is sentinel_live


def test_snapshot_sentinel_parses_balance_from_memo() -> None:
    memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=1234560,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=_PRIOR,
        drift_milliunits_stronger=0,
        stronger_currency="USD",
    )
    transaction = _txn(
        txn_id="sent-1",
        amount_milliunits=0,
        memo=memo,
        payee_name=SENTINEL_PAYEE_NAME,
        cleared="reconciled",
    )

    snapshot = snapshot_sentinel(transaction)

    assert snapshot is not None
    assert snapshot.balance_milliunits == 1234560


def test_snapshot_sentinel_returns_none_when_memo_unparseable() -> None:
    transaction = _txn(
        txn_id="sent-1",
        amount_milliunits=0,
        memo="not a sentinel memo",
        payee_name=SENTINEL_PAYEE_NAME,
    )

    assert snapshot_sentinel(transaction) is None


def test_build_tracking_update_adds_new_cleared_transaction() -> None:
    plan = _plan()
    txn = _txn(txn_id="t1", amount_milliunits=12340, cleared="cleared")
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(cleared_balance_milliunits=1582),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.prior_balance_milliunits == 0
    assert result.new_balance_milliunits == 12340
    assert len(result.contributions) == 1
    assert result.contributions[0].reason == "count-new"
    assert result.create_sentinel is not None
    assert result.create_sentinel.flag_color == SENTINEL_FLAG_COLOR
    assert result.update_sentinel is None
    assert result.within_tolerance is True
    # Unmarked rows get their ``[FX+]`` marker from the FX conversion path
    # (not from a tracking memo flip), so the tracking update emits no flips.
    assert result.memo_flips == ()


def test_sentinel_create_carries_the_green_flag() -> None:
    plan = _plan()

    # First run: no prior sentinel → create request carries the flag.
    create_result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[_txn(txn_id="t1", amount_milliunits=5000, cleared="cleared")],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )
    assert create_result.create_sentinel is not None
    assert create_result.create_sentinel.flag_color == "green"
    assert create_result.update_sentinel is None

    # Quiet delta: prior sentinel already matches the desired balance, so no write is needed.
    sentinel_memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=5000,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=_PRIOR,
        drift_milliunits_stronger=0,
        stronger_currency="USD",
    )
    update_result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[
            _txn(
                txn_id="sentinel",
                amount_milliunits=0,
                memo=sentinel_memo,
                payee_name=SENTINEL_PAYEE_NAME,
                cleared="reconciled",
            ),
        ],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )
    assert update_result.create_sentinel is None
    assert update_result.update_sentinel is None


def test_build_tracking_update_ignores_uncleared_new_transaction() -> None:
    plan = _plan()
    txn = _txn(txn_id="t1", amount_milliunits=12340, cleared="uncleared")
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 0
    assert result.contributions == ()


def test_build_tracking_update_subtracts_counted_then_deleted() -> None:
    """A previously-counted row (``[FX+]``) that is now deleted gets subtracted
    from the balance.

    YNAB refuses ``update_transactions`` calls targeting soft-deleted rows
    (400 "transaction does not exist in this budget"), so the balance engine
    must NOT emit a memo flip for a deleted transaction even though the
    bracket technically disagrees with ``should_be_counted``. Deleted is a
    terminal state in delta sync, so the stale ``[FX+]`` bracket on a dead
    row is harmless.
    """
    plan = _plan()
    memo = "Coffee | [FX+] -12.34 HKD (rate: 7.8 HKD/USD)"
    txn = _txn(
        txn_id="t1",
        amount_milliunits=-1582,
        memo=memo,
        cleared="cleared",
        deleted=True,
    )
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 12340  # -(-12340) added back
    assert result.contributions[0].reason == "uncount"
    # No memo flip emitted for a deleted row.
    assert result.memo_flips == ()


def test_build_tracking_update_skips_memo_flip_on_deleted_uncleared_counted() -> None:
    """Same invariant as above but via a different transition path.

    A row that had ``[FX+]`` + uncleared (an unusual state, but possible when
    a user un-clears before deleting) should still be subtracted and left
    alone memo-wise on delete. The point is that deleted rows are never
    written to.
    """
    plan = _plan()
    txn = _txn(
        txn_id="t2",
        amount_milliunits=-1582,
        memo="[FX+] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="uncleared",
        deleted=True,
    )
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )
    assert result.memo_flips == ()
    assert result.contributions[0].reason == "uncount"
    assert result.new_balance_milliunits == 12340


def test_rebuild_skips_memo_flip_on_deleted_row() -> None:
    """Rebuild mode must also avoid writing to deleted rows even if their
    bracket disagrees with the current should_be_counted state."""
    plan = _plan()
    # [FX+] on a deleted row: rebuild would want to flip to [FX], but we skip.
    txn = _txn(
        txn_id="deleted-counted",
        amount_milliunits=-1582,
        memo="[FX+] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
        deleted=True,
    )
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )
    # Deleted row is not counted in rebuild mode (should_be_counted=False),
    # and the memo is left alone.
    assert result.contributions == ()
    assert result.memo_flips == ()


def test_delta_adds_marked_uncounted_cleared_row_and_flips_to_counted() -> None:
    """Regression: a cleared-state row whose marker is still ``[FX]`` (uncounted)
    must contribute on the next delta and have its marker flipped to ``[FX+]``.

    This covers the user-reported drift where an already-FX'd but uncounted
    row's cleared status changed between runs. The dual-marker model handles
    it by using the marker as the ledger: was_counted=False, should=True →
    add + flip to counted.
    """
    plan = _plan()
    already_marked = _txn(
        txn_id="reclassified",
        amount_milliunits=-1582,
        memo="Lunch | [FX] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[already_marked],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == -12340
    assert len(result.contributions) == 1
    assert result.contributions[0].reason == "count"
    assert len(result.memo_flips) == 1
    flip = result.memo_flips[0]
    assert flip.transaction_id == "reclassified"
    assert "[FX+]" in flip.memo
    assert flip.amount_milliunits is None  # amount must not be touched


def test_delta_transfer_outflow_leaves_directional_marker_when_pair_is_partial() -> None:
    plan = _plan()
    transfer = _txn(
        txn_id="transfer-out",
        amount_milliunits=-1582,
        memo="Move | [FX→] +/-12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
        transfer_id="transfer-in",
        paired_transfer_counted=False,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[transfer],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.contributions == ()
    assert result.memo_flips == ()


def test_delta_transfer_inflow_flip_promotes_arrow_marker_to_both_counted() -> None:
    plan = _plan()
    transfer = _txn(
        txn_id="transfer-in",
        amount_milliunits=1582,
        memo="Move | [FX→] +/-12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
        transfer_id="transfer-out",
        paired_transfer_counted=True,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[transfer],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert len(result.contributions) == 1
    assert result.contributions[0].signed_source_milliunits == 12340
    assert len(result.memo_flips) == 1
    assert "[FX+]" in result.memo_flips[0].memo


def test_delta_transfer_unclearing_demotes_both_counted_marker_to_arrow() -> None:
    plan = _plan()
    transfer = _txn(
        txn_id="transfer-in",
        amount_milliunits=1582,
        memo="Move | [FX+] +/-12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="uncleared",
        transfer_id="transfer-out",
        paired_transfer_counted=True,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[transfer],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert len(result.contributions) == 1
    assert result.contributions[0].signed_source_milliunits == -12340
    assert len(result.memo_flips) == 1
    assert "[FX→]" in result.memo_flips[0].memo


def test_delta_cleared_to_reconciled_is_noop_on_counted_row() -> None:
    """Regression for the double-count bug the dual-marker model fixes.

    A row that is already ``[FX+]`` and stays cleared/reconciled must NOT
    re-contribute when YNAB re-surfaces it (e.g. because the user moved it
    from cleared → reconciled). Under the prior "every cleared delta row
    contributes" rule this produced a silent double-count.
    """
    plan = _plan()
    already_counted = _txn(
        txn_id="pre-counted",
        amount_milliunits=-1582,
        memo="Lunch | [FX+] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="reconciled",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[already_counted],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 0
    assert result.contributions == ()
    assert result.memo_flips == ()


def test_delta_counted_to_uncleared_subtracts_and_flips_back() -> None:
    """``cleared → uncleared`` on a ``[FX+]`` row: subtract and flip to ``[FX]``.

    The original spec listed this as a supported transition; the dual-marker
    model delivers it without any local state.
    """
    plan = _plan()
    counted_uncleared = _txn(
        txn_id="reopen",
        amount_milliunits=-1582,
        memo="Lunch | [FX+] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="uncleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[counted_uncleared],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 12340
    assert len(result.contributions) == 1
    assert result.contributions[0].signed_source_milliunits == 12340
    assert result.contributions[0].reason == "uncount"
    assert len(result.memo_flips) == 1
    flip = result.memo_flips[0]
    assert "[FX]" in flip.memo
    assert "[FX+]" not in flip.memo


def test_delta_migrates_legacy_marker_to_counted_and_contributes() -> None:
    """A legacy ``(FX rate: ...)`` marker on a cleared row gets migrated
    straight to ``[FX+]`` form while the row's source amount is added to the
    balance, so subsequent runs don't re-examine it."""
    plan = _plan()
    legacy_cleared = _txn(
        txn_id="legacy-1",
        amount_milliunits=10000,
        memo="Coffee | 78 HKD (FX rate: 7.8)",
        cleared="cleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[legacy_cleared],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 78000
    assert len(result.memo_flips) == 1
    flip = result.memo_flips[0]
    assert "[FX+]" in flip.memo
    assert "FX rate" not in flip.memo


def test_delta_migrates_legacy_marker_to_uncounted_on_uncleared_row() -> None:
    """Legacy + uncleared: migrate the marker to ``[FX]`` without contributing.

    Ensures future runs don't keep re-visiting the row (was_counted=False,
    should=False, but the memo is legacy, so we still emit a migration flip).
    """
    plan = _plan()
    legacy_uncleared = _txn(
        txn_id="legacy-u",
        amount_milliunits=-7000,
        memo="Notes | 54 HKD (FX rate: 7.8)",
        cleared="uncleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[legacy_uncleared],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.contributions == ()
    assert result.new_balance_milliunits == 0
    assert len(result.memo_flips) == 1
    flip = result.memo_flips[0]
    assert "[FX]" in flip.memo
    assert "[FX+]" not in flip.memo
    assert "FX rate" not in flip.memo


def test_build_tracking_update_uncleared_uncounted_row_is_noop() -> None:
    """An uncleared row whose marker is ``[FX]`` (uncounted) never touched the
    balance before and doesn't this run either: was=False, should=False."""
    plan = _plan()
    memo = "Coffee | [FX] -12.34 HKD (rate: 7.8 HKD/USD)"
    txn = _txn(
        txn_id="t1",
        amount_milliunits=-1582,
        memo=memo,
        cleared="uncleared",
    )
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 0
    assert result.contributions == ()
    assert result.memo_flips == ()


def test_build_tracking_update_carries_prior_balance_and_updates_sentinel() -> None:
    plan = _plan()
    sentinel_memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=1000000,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=_PRIOR,
        drift_milliunits_stronger=0,
        stronger_currency="USD",
    )
    sentinel = _txn(
        txn_id="sentinel",
        amount_milliunits=0,
        memo=sentinel_memo,
        payee_name=SENTINEL_PAYEE_NAME,
        cleared="reconciled",
    )
    new_txn = _txn(txn_id="t1", amount_milliunits=50000, cleared="cleared")

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[sentinel, new_txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.prior_sentinel is not None
    assert result.prior_balance_milliunits == 1000000
    assert result.new_balance_milliunits == 1050000
    assert result.create_sentinel is None
    assert result.update_sentinel is not None
    assert result.update_sentinel.transaction_id == "sentinel"
    assert result.update_sentinel.memo == "[YMCA-BAL] HKD 1,050.00"
    assert result.update_sentinel.flag_color == "green"


def test_build_tracking_update_skips_sentinel_from_contributions() -> None:
    plan = _plan()
    sentinel = _txn(
        txn_id="sentinel",
        amount_milliunits=0,
        memo="not parseable",
        payee_name=SENTINEL_PAYEE_NAME,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[sentinel],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.contributions == ()
    assert result.prior_sentinel is None  # unparseable memo treated as no prior balance
    assert result.update_sentinel is not None  # sentinel id still gets an update write


def test_build_tracking_update_skips_split_transactions() -> None:
    plan = _plan()
    txn = _txn(txn_id="t-split", amount_milliunits=50000, cleared="cleared")

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[txn],
        split_skipped_ids={"t-split"},
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.contributions == ()
    assert result.new_balance_milliunits == 0


def test_build_tracking_update_rebuild_sums_markers_from_scratch() -> None:
    plan = _plan()
    # Sentinel exists but has wrong balance; rebuild should override it.
    sentinel_memo = build_sentinel_memo(
        currency="HKD",
        balance_milliunits=99999999,
        rate_text="7.8",
        pair_label="HKD/USD",
        updated_at=_PRIOR,
        drift_milliunits_stronger=0,
        stronger_currency="USD",
    )
    sentinel = _txn(
        txn_id="sentinel",
        amount_milliunits=0,
        memo=sentinel_memo,
        payee_name=SENTINEL_PAYEE_NAME,
        cleared="reconciled",
    )
    legacy_txn = _txn(
        txn_id="legacy",
        amount_milliunits=10000,
        memo="78 HKD (FX rate: 0.12821)",
        cleared="cleared",
    )
    current_txn = _txn(
        txn_id="current",
        amount_milliunits=-1582,
        memo="Coffee | [FX] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
    )
    unmarked_cleared = _txn(
        txn_id="unmarked",
        amount_milliunits=50000,
        cleared="cleared",
    )
    deleted_cleared = _txn(
        txn_id="deleted",
        amount_milliunits=-1582,
        memo="stale | [FX] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
        deleted=True,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[sentinel, legacy_txn, current_txn, unmarked_cleared, deleted_cleared],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    # legacy 78.00 HKD = 78000 + current -12.34 = -12340 + unmarked 50.00 = 50000
    assert result.new_balance_milliunits == 78000 - 12340 + 50000
    reasons = {c.reason for c in result.contributions}
    assert reasons == {"rebuild-marked", "rebuild-unmarked"}
    # Sentinel gets overwritten (update_sentinel populated).
    assert result.update_sentinel is not None


def test_build_tracking_update_rebuild_skips_deleted_marked_rows() -> None:
    plan = _plan()
    deleted_txn = _txn(
        txn_id="deleted",
        amount_milliunits=-1582,
        memo="Coffee | [FX] -12.34 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
        deleted=True,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[deleted_txn],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.contributions == ()


def test_zero_amount_transfer_rebuild_prompts_and_uses_resolved_direction() -> None:
    plan = _plan()
    memo = "Move | [FX] +/-12.34 HKD (rate: 7.8 HKD/USD)"
    zero_transfer = _txn(
        txn_id="zero-xfer",
        amount_milliunits=0,
        memo=memo,
        cleared="cleared",
        transfer_id="other-side",
    )

    prompted: list[str] = []

    def prompt(event):  # type: ignore[no-untyped-def]
        prompted.append(event.transaction_id)
        return -1

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[zero_transfer],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=prompt,
    )

    assert prompted == ["zero-xfer"]
    assert result.new_balance_milliunits == -12340
    assert result.ambiguous_transfers  # still recorded for visibility


def test_zero_amount_transfer_rebuild_without_prompt_returns_ambiguous_and_skips() -> None:
    plan = _plan()
    memo = "Move | [FX] +/-12.34 HKD (rate: 7.8 HKD/USD)"
    zero_transfer = _txn(
        txn_id="zero-xfer",
        amount_milliunits=0,
        memo=memo,
        cleared="cleared",
        transfer_id="other-side",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[zero_transfer],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == 0
    assert result.contributions == ()
    assert len(result.ambiguous_transfers) == 1
    assert result.ambiguous_transfers[0].memo_amount_milliunits == 12340


def test_rebuild_overrides_memo_sign_with_ynab_amount_sign() -> None:
    """Stale or hand-edited memo signs must not drive the balance.

    Regression: a transfer outflow of -$417.09 with memo ``[FX] +2,919.64 RMB``
    used to contribute ``+2,919.64`` (trusting the explicit ``+`` in the memo),
    which inflated the account's tracked balance by double the magnitude.
    """
    plan = _plan()
    # Positive-signed memo but negative (outflow) YNAB amount.
    stale_memo_positive = _txn(
        txn_id="stale-pos",
        amount_milliunits=-41709,
        memo="Transfer | [FX] +2,919.64 RMB (rate: 0.14286 USD/RMB)",
        cleared="reconciled",
        transfer_id="other-leg",
    )
    # Negative-signed memo but positive (inflow) YNAB amount (also unusual,
    # covered for symmetry).
    stale_memo_negative = _txn(
        txn_id="stale-neg",
        amount_milliunits=50590,
        memo="[FX] -354.13 RMB (rate: 0.14286 USD/RMB)",
        cleared="reconciled",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[stale_memo_positive, stale_memo_negative],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    by_id = {c.transaction_id: c for c in result.contributions}
    # YNAB outflow overrides the ``+`` in the memo → contribution is negative.
    assert by_id["stale-pos"].signed_source_milliunits == -2919640
    # YNAB inflow overrides the ``-`` in the memo → contribution is positive.
    assert by_id["stale-neg"].signed_source_milliunits == 354130


def test_zero_amount_non_transfer_rebuild_uses_memo_sign() -> None:
    # E21: 0-amount non-transfer whose memo still carries a signed source amount.
    plan = _plan()
    zero_non_transfer = _txn(
        txn_id="zero-normal",
        amount_milliunits=0,
        memo="[FX] -0.25 HKD (rate: 7.8 HKD/USD)",
        cleared="cleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[zero_non_transfer],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.new_balance_milliunits == -250


def test_build_tracking_update_flags_drift_beyond_tolerance() -> None:
    plan = _plan()
    # Tracked 100 HKD ≈ 12.82 USD, but YNAB says 14.00 USD cleared. Drift = -1.18 USD.
    txn = _txn(txn_id="t1", amount_milliunits=100000, cleared="cleared")
    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(cleared_balance_milliunits=14000),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.within_tolerance is False
    assert result.drift_milliunits_stronger < -TOLERANCE_STRONGER_MILLIUNITS


@pytest.mark.parametrize(
    ("direction_return", "expected_signed"),
    [(1, 1000), (-1, -1000), (None, 0), (0, 0)],
)
def test_prompt_outputs_shape_contribution(
    direction_return: int | None, expected_signed: int
) -> None:
    plan = _plan()
    memo = "Move | [FX] +/-1.00 HKD (rate: 7.8 HKD/USD)"
    zero_transfer = _txn(
        txn_id="zero-xfer",
        amount_milliunits=0,
        memo=memo,
        cleared="cleared",
        transfer_id="other",
    )

    def prompt(event):  # type: ignore[no-untyped-def]
        del event
        return direction_return

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[zero_transfer],
        split_skipped_ids=set(),
        rebuild=True,
        now_utc=_NOW,
        prompt_for_transfer_direction=prompt,
    )

    assert result.new_balance_milliunits == expected_signed


def test_build_tracking_update_unparseable_prior_sentinel_still_upserts() -> None:
    plan = _plan()
    sentinel = _txn(
        txn_id="broken",
        amount_milliunits=0,
        memo="definitely not a sentinel memo",
        payee_name=SENTINEL_PAYEE_NAME,
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[sentinel],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.prior_sentinel is None
    assert result.update_sentinel is not None
    assert result.update_sentinel.transaction_id == "broken"


def test_build_tracking_update_repairs_nonzero_sentinel_amount() -> None:
    plan = _plan()
    sentinel = _txn(
        txn_id="sentinel",
        amount_milliunits=5000,
        memo="[YMCA-BAL] HKD 5.00",
        payee_name=SENTINEL_PAYEE_NAME,
        cleared="reconciled",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[0],
        account_id="acct-hkd",
        remote_account=_hkd_account(),
        transactions=[sentinel],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.update_sentinel is not None
    assert result.update_sentinel.amount_milliunits == 0
    assert result.update_sentinel.memo == "[YMCA-BAL] HKD 5.00"


def test_gbp_tracking_drift_reports_in_gbp() -> None:
    plan = _plan()
    txn = RemoteTransaction(
        id="gbp-1",
        date=date(2026, 4, 10),
        amount_milliunits=100000,
        memo=None,
        account_id="acct-gbp",
        transfer_account_id=None,
        transfer_transaction_id=None,
        deleted=False,
        payee_name=None,
        cleared="cleared",
    )

    result = build_tracking_update(
        plan=plan,
        account=plan.accounts[1],
        account_id="acct-gbp",
        remote_account=_gbp_account(cleared_balance_milliunits=135000),
        transactions=[txn],
        split_skipped_ids=set(),
        rebuild=False,
        now_utc=_NOW,
        prompt_for_transfer_direction=None,
    )

    assert result.stronger_currency == "GBP"
    assert result.within_tolerance is True
