"""Local-currency balance tracking for the ``ymca sync`` pipeline.

This module layers on top of the FX conversion flow in :mod:`ymca.conversion`.
It computes per-account balance contributions for each run, detects the
in-account sentinel transaction, and produces sentinel create/update requests
plus a tolerance-check summary.

Design follows ``docs/spec.md`` §12. The classifier is driven by a dual-marker
model: the FX memo carries either ``[FX]`` (converted but uncounted) or
``[FX+]`` (converted and already added to the tracked balance). The engine
compares ``was_counted`` (derived from the bracket) against
``should_be_counted`` (derived from the current cleared/deleted YNAB state)
and acts on the four-way truth table:

* ``was=False``, ``should=True``  → add, flip the marker to ``[FX+]``.
* ``was=True``, ``should=False`` → subtract, flip the marker to ``[FX]``.
* else → no-op.

Rebuild mode (``rebuild=True``) ignores the prior counted flag and re-derives
both the balance and the marker state from the current cleared/deleted
status, then writes absolute results back through the sentinel + memo flips.

The module does not make any network calls or write side effects by itself;
callers pass in the fetched transactions and the remote account snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from .memo import (
    SENTINEL_FLAG_COLOR,
    SENTINEL_PAYEE_NAME,
    build_sentinel_memo,
    flip_fx_marker_counted,
    has_fx_counted_marker,
    has_fx_marker,
    has_legacy_fx_marker,
    is_sentinel_payee,
    memo_marker_has_transfer_prefix,
    parse_sentinel_memo,
    replace_legacy_fx_marker,
    source_amount_milliunits_from_marker,
)
from .models import (
    AccountConfig,
    AmbiguousTransfer,
    BalanceContribution,
    FxRule,
    NewTransactionRequest,
    PlanConfig,
    PreparedTrackingUpdate,
    RemoteAccount,
    RemoteTransaction,
    SentinelSnapshot,
    TransactionUpdateRequest,
)

TransferDirectionPrompt = Callable[[AmbiguousTransfer], int | None]
"""Callback that resolves a 0-amount transfer's direction.

Returns ``1`` for inflow, ``-1`` for outflow, ``None`` to skip the row.
"""

TOLERANCE_STRONGER_MILLIUNITS = 10
"""0.01 stronger-currency units expressed in milliunits."""


def stronger_currency(rule: FxRule, *, base_currency: str, source_currency: str) -> str:
    """Return the currency where 1 unit is worth more, per FX rule direction."""
    return base_currency if rule.divide_to_base else source_currency


def compute_drift_milliunits_stronger(
    *,
    tracked_source_milliunits: int,
    ynab_cleared_balance_base_milliunits: int,
    rule: FxRule,
) -> int:
    """Return signed drift in **stronger-currency milliunits**.

    Drift is defined as ``tracked - cleared`` with both operands converted to
    the stronger currency. For ``divide_to_base=true`` the stronger currency
    is the base, and the tracked source balance is divided by ``rate`` to
    bring it into base units. For ``divide_to_base=false`` the stronger
    currency is the source, and YNAB's base-denominated ``cleared_balance``
    is divided by ``rate`` to bring it into source units.
    """
    if rule.divide_to_base:
        tracked_in_stronger = Decimal(tracked_source_milliunits) / rule.rate
        cleared_in_stronger = Decimal(ynab_cleared_balance_base_milliunits)
    else:
        tracked_in_stronger = Decimal(tracked_source_milliunits)
        cleared_in_stronger = Decimal(ynab_cleared_balance_base_milliunits) / rule.rate
    drift = tracked_in_stronger - cleared_in_stronger
    return int(drift.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def within_tolerance(drift_milliunits_stronger: int) -> bool:
    """Return True when drift is within 0.01 stronger-currency units."""
    return abs(drift_milliunits_stronger) <= TOLERANCE_STRONGER_MILLIUNITS


def find_existing_sentinel(
    transactions: Iterable[RemoteTransaction],
) -> RemoteTransaction | None:
    """Return the first non-deleted sentinel transaction found, or None."""
    for transaction in transactions:
        if transaction.deleted:
            continue
        if is_sentinel_payee(transaction.payee_name):
            return transaction
    return None


def snapshot_sentinel(transaction: RemoteTransaction) -> SentinelSnapshot | None:
    """Parse a sentinel transaction's memo into a :class:`SentinelSnapshot`.

    Returns ``None`` when the memo does not match the sentinel shape; callers
    should treat that as "no prior balance" and overwrite on the next write.
    """
    parsed = parse_sentinel_memo(transaction.memo)
    if parsed is None:
        return None
    raw_balance = parsed["balance_milliunits"]
    if not isinstance(raw_balance, int):
        return None
    balance_milliunits = raw_balance
    return SentinelSnapshot(
        id=transaction.id,
        date=transaction.date,
        memo=transaction.memo or "",
        cleared=transaction.cleared,
        deleted=transaction.deleted,
        balance_milliunits=balance_milliunits,
    )


def build_tracking_update(
    *,
    plan: PlanConfig,
    account: AccountConfig,
    account_id: str,
    remote_account: RemoteAccount,
    transactions: Iterable[RemoteTransaction],
    split_skipped_ids: set[str],
    rebuild: bool,
    now_utc: datetime,
    prompt_for_transfer_direction: TransferDirectionPrompt | None,
) -> PreparedTrackingUpdate:
    """Build the :class:`PreparedTrackingUpdate` for a single tracked account.

    Arguments:
        plan: full plan config (used for fx rule lookup)
        account: the tracked account config
        account_id: resolved YNAB account id
        remote_account: remote account snapshot (used for cleared_balance)
        transactions: delta (or full-scan) transactions for this account
        split_skipped_ids: ids already classified as split by the conversion
            pipeline; they never contribute to the tracked balance.
        rebuild: when True, run in full-scan mode.
        now_utc: clock reading used for the sentinel ``updated`` timestamp.
        prompt_for_transfer_direction: callback invoked for every 0-amount
            transfer whose direction cannot be inferred from the YNAB amount.

    Returns a fully-resolved :class:`PreparedTrackingUpdate` that the execute
    phase can apply without further lookups.
    """
    rule = plan.fx_rates[account.currency]
    pair_label = rule.pair_label(
        base_currency=plan.base_currency, source_currency=account.currency
    )
    stronger = stronger_currency(
        rule, base_currency=plan.base_currency, source_currency=account.currency
    )

    materialized = list(transactions)
    prior_sentinel_txn = find_existing_sentinel(materialized)
    prior_sentinel: SentinelSnapshot | None = None
    if prior_sentinel_txn is not None:
        prior_sentinel = snapshot_sentinel(prior_sentinel_txn)
    prior_balance = prior_sentinel.balance_milliunits if prior_sentinel is not None else 0

    contributions: list[BalanceContribution] = []
    memo_flips: list[TransactionUpdateRequest] = []
    ambiguous: list[AmbiguousTransfer] = []
    sentinel_id = prior_sentinel_txn.id if prior_sentinel_txn is not None else None

    for transaction in materialized:
        if transaction.id == sentinel_id:
            continue
        if is_sentinel_payee(transaction.payee_name):
            continue
        if transaction.id in split_skipped_ids:
            continue
        contribution, memo_flip = _classify_transaction(
            transaction=transaction,
            account=account,
            pair_label=pair_label,
            rebuild=rebuild,
            prompt=prompt_for_transfer_direction,
            ambiguous_out=ambiguous,
        )
        if contribution is not None:
            contributions.append(contribution)
        if memo_flip is not None:
            memo_flips.append(memo_flip)

    if rebuild:
        new_balance = sum(c.signed_source_milliunits for c in contributions)
    else:
        new_balance = prior_balance + sum(c.signed_source_milliunits for c in contributions)

    drift = compute_drift_milliunits_stronger(
        tracked_source_milliunits=new_balance,
        ynab_cleared_balance_base_milliunits=remote_account.cleared_balance_milliunits,
        rule=rule,
    )

    new_memo = build_sentinel_memo(
        currency=account.currency,
        balance_milliunits=new_balance,
    )

    create_request: NewTransactionRequest | None = None
    update_request: TransactionUpdateRequest | None = None
    if prior_sentinel_txn is None:
        create_request = NewTransactionRequest(
            account_id=account_id,
            date=now_utc.date(),
            amount_milliunits=0,
            memo=new_memo,
            payee_name=SENTINEL_PAYEE_NAME,
            cleared="reconciled",
            flag_color=SENTINEL_FLAG_COLOR,
        )
    else:
        sentinel_needs_update = (
            prior_sentinel_txn.memo != new_memo or prior_sentinel_txn.amount_milliunits != 0
        )
        if sentinel_needs_update:
            update_request = TransactionUpdateRequest(
                transaction_id=prior_sentinel_txn.id,
                amount_milliunits=0,
                memo=new_memo,
                flag_color=SENTINEL_FLAG_COLOR,
            )

    return PreparedTrackingUpdate(
        account_alias=account.alias,
        currency=account.currency,
        account_id=account_id,
        account_name=account.name,
        prior_sentinel=prior_sentinel,
        prior_balance_milliunits=prior_balance,
        contributions=tuple(contributions),
        ambiguous_transfers=tuple(ambiguous),
        new_balance_milliunits=new_balance,
        ynab_cleared_balance_milliunits=remote_account.cleared_balance_milliunits,
        stronger_currency=stronger,
        drift_milliunits_stronger=drift,
        within_tolerance=within_tolerance(drift),
        rebuild=rebuild,
        create_sentinel=create_request,
        update_sentinel=update_request,
        memo_flips=tuple(memo_flips),
    )


def _classify_transaction(
    *,
    transaction: RemoteTransaction,
    account: AccountConfig,
    pair_label: str,
    rebuild: bool,
    prompt: TransferDirectionPrompt | None,
    ambiguous_out: list[AmbiguousTransfer],
) -> tuple[BalanceContribution | None, TransactionUpdateRequest | None]:
    """Classify one transaction using the dual-marker rule (spec §12).

    Returns a ``(contribution, memo_flip)`` pair. Either side can be ``None``:

    * ``contribution is None`` means the row does not move the tracked
      balance this run.
    * ``memo_flip is None`` means the FX marker on the row already matches
      its current counted state (or there is no marker to flip -- the FX
      conversion path writes a fresh ``[FX]``/``[FX+]`` marker in that case).

    In ``rebuild`` mode the prior counted flag is ignored: the balance is
    re-derived from scratch from the current cleared/deleted status, and any
    marker whose bracket does not match that status is flipped in place.
    """
    is_current = has_fx_marker(transaction.memo)
    is_legacy = (not is_current) and has_legacy_fx_marker(transaction.memo)
    was_counted = has_fx_counted_marker(transaction.memo)
    is_cleared = transaction.cleared in ("cleared", "reconciled")
    should_be_counted = is_cleared and not transaction.deleted

    if rebuild:
        return _classify_rebuild(
            transaction=transaction,
            account=account,
            pair_label=pair_label,
            is_current=is_current,
            is_legacy=is_legacy,
            was_counted=was_counted,
            should_be_counted=should_be_counted,
            prompt=prompt,
            ambiguous_out=ambiguous_out,
        )
    return _classify_delta(
        transaction=transaction,
        account=account,
        pair_label=pair_label,
        is_current=is_current,
        is_legacy=is_legacy,
        was_counted=was_counted,
        should_be_counted=should_be_counted,
        prompt=prompt,
        ambiguous_out=ambiguous_out,
    )


def _classify_delta(
    *,
    transaction: RemoteTransaction,
    account: AccountConfig,
    pair_label: str,
    is_current: bool,
    is_legacy: bool,
    was_counted: bool,
    should_be_counted: bool,
    prompt: TransferDirectionPrompt | None,
    ambiguous_out: list[AmbiguousTransfer],
) -> tuple[BalanceContribution | None, TransactionUpdateRequest | None]:
    """Delta-mode classifier built around the was_counted x should_be_counted 2x2.

    * ``was=False, should=True``  → add source amount, flip marker to ``[FX+]``
      (migrate legacy → ``[FX+]`` if needed).
    * ``was=True,  should=False`` → subtract source amount, flip marker to
      ``[FX]``.
    * ``was=True,  should=True``  → no-op (prevents the double-count that
      tripped the ``cleared → reconciled`` edge case).
    * ``was=False, should=False`` → no-op (row never contributed and still
      shouldn't). Legacy markers stuck in this cell get migrated to ``[FX]``
      so future runs don't re-consider them.
    """
    if not is_current and not is_legacy:
        # Unmarked: the FX conversion path writes a fresh [FX+]/[FX] marker
        # this run (see ``_prepare_update``), so we do not emit a separate
        # memo flip here. Contribute only when the row is cleared+not-deleted;
        # otherwise the FX path writes [FX] with no balance effect.
        if should_be_counted:
            return (
                BalanceContribution(
                    transaction_id=transaction.id,
                    signed_source_milliunits=transaction.amount_milliunits,
                    reason="count-new",
                ),
                None,
            )
        return None, None

    if is_current and was_counted == should_be_counted:
        # No change to counted state and the memo is already in current form.
        return None, None

    amount = _resolve_marked_source_milliunits(
        transaction=transaction,
        account=account,
        prompt=prompt,
        ambiguous_out=ambiguous_out,
    )
    if amount is None:
        return None, None

    # YNAB refuses to touch soft-deleted rows via ``update_transactions``
    # ("transaction does not exist in this budget"), so we skip the memo
    # flip on deleted rows. The balance side still records the ``uncount``
    # contribution; deleted rows are a terminal state in delta sync and
    # won't re-appear in future deltas, so the stale ``[FX+]`` marker on
    # a dead row is harmless.
    memo_flip: TransactionUpdateRequest | None = None
    if not transaction.deleted:
        new_memo = _rewrite_marker(
            memo=transaction.memo,
            is_current=is_current,
            is_legacy=is_legacy,
            counted=should_be_counted,
            source_currency=account.currency,
            pair_label=pair_label,
            is_transfer=transaction.transfer_transaction_id is not None,
        )
        if new_memo is not None and new_memo != transaction.memo:
            memo_flip = TransactionUpdateRequest(
                transaction_id=transaction.id,
                amount_milliunits=None,
                memo=new_memo,
            )

    if should_be_counted and not was_counted:
        return (
            BalanceContribution(
                transaction_id=transaction.id,
                signed_source_milliunits=amount,
                reason="count",
            ),
            memo_flip,
        )
    if was_counted and not should_be_counted:
        return (
            BalanceContribution(
                transaction_id=transaction.id,
                signed_source_milliunits=-amount,
                reason="uncount",
            ),
            memo_flip,
        )

    # was=False, should=False on a legacy row: migrate the memo without
    # touching the balance so the row doesn't need re-examination next run.
    return None, memo_flip


def _classify_rebuild(
    *,
    transaction: RemoteTransaction,
    account: AccountConfig,
    pair_label: str,
    is_current: bool,
    is_legacy: bool,
    was_counted: bool,
    should_be_counted: bool,
    prompt: TransferDirectionPrompt | None,
    ambiguous_out: list[AmbiguousTransfer],
) -> tuple[BalanceContribution | None, TransactionUpdateRequest | None]:
    """Rebuild classifier: derive both balance and marker from current state.

    The running balance starts at zero in ``build_tracking_update``, so this
    branch only emits **positive** contributions for rows that currently
    should be counted. Marker state is normalized regardless of whether it
    moves the balance, so legacy markers get migrated and stale ``[FX+]``
    brackets on uncleared rows get flipped back to ``[FX]``.
    """
    # Marker flip (independent of the balance direction). YNAB refuses
    # updates to soft-deleted rows, so we never emit a flip for ``deleted``
    # transactions even if their bracket disagrees with ``should_be_counted``.
    new_memo = None
    if not transaction.deleted:
        if is_current and was_counted != should_be_counted:
            new_memo = flip_fx_marker_counted(
                transaction.memo or "", counted=should_be_counted
            )
        elif is_legacy:
            new_memo = replace_legacy_fx_marker(
                transaction.memo or "",
                pair_label_for_currency={account.currency: pair_label},
                transfer=transaction.transfer_transaction_id is not None,
                counted=should_be_counted,
            )

    memo_flip: TransactionUpdateRequest | None = None
    if new_memo is not None and new_memo != (transaction.memo or ""):
        memo_flip = TransactionUpdateRequest(
            transaction_id=transaction.id,
            amount_milliunits=None,
            memo=new_memo,
        )

    if not should_be_counted:
        return None, memo_flip

    # Count the row. For unmarked rows the FX path will write the memo; for
    # marked rows we rely on the parsed source amount.
    if not is_current and not is_legacy:
        return (
            BalanceContribution(
                transaction_id=transaction.id,
                signed_source_milliunits=transaction.amount_milliunits,
                reason="rebuild-unmarked",
            ),
            None,
        )

    amount = _resolve_marked_source_milliunits(
        transaction=transaction,
        account=account,
        prompt=prompt,
        ambiguous_out=ambiguous_out,
    )
    if amount is None:
        return None, memo_flip
    return (
        BalanceContribution(
            transaction_id=transaction.id,
            signed_source_milliunits=amount,
            reason="rebuild-marked",
        ),
        memo_flip,
    )


def _rewrite_marker(
    *,
    memo: str | None,
    is_current: bool,
    is_legacy: bool,
    counted: bool,
    source_currency: str,
    pair_label: str,
    is_transfer: bool,
) -> str | None:
    """Produce the memo with the FX marker flipped to the requested counted
    state. Handles both the current ``[FX]``/``[FX+]`` form and legacy
    ``(FX rate: ...)`` migration. Returns ``None`` when there is nothing to
    rewrite.
    """
    source_memo = memo or ""
    if is_current:
        return flip_fx_marker_counted(source_memo, counted=counted)
    if is_legacy:
        return replace_legacy_fx_marker(
            source_memo,
            pair_label_for_currency={source_currency: pair_label},
            transfer=is_transfer,
            counted=counted,
        )
    return None


def _resolve_marked_source_milliunits(
    *,
    transaction: RemoteTransaction,
    account: AccountConfig,
    prompt: TransferDirectionPrompt | None,
    ambiguous_out: list[AmbiguousTransfer],
) -> int | None:
    """Return signed source milliunits for a previously-converted transaction.

    Sign inference (per §12.7):

    1. **YNAB amount sign is the ground truth** whenever the amount is
       non-zero. Any sign embedded in the memo is discarded in favor of the
       YNAB-side sign; this catches stale or hand-edited memos where, for
       example, a transfer outflow ended up with a ``+`` in the FX marker.
    2. For non-transfer zero-amount rows, fall back to the sign embedded in
       the memo (there is no other signal to use).
    3. For 0-amount transfer rows with a ``+/-`` literal in the memo, the
       direction is ambiguous: record the row and delegate to ``prompt``.
    """
    has_plus_minus = memo_marker_has_transfer_prefix(transaction.memo)
    is_transfer = transaction.transfer_transaction_id is not None

    # 1. Non-zero YNAB amount: override any memo sign with the YNAB sign.
    if transaction.amount_milliunits != 0:
        ynab_sign = 1 if transaction.amount_milliunits > 0 else -1
        # Pass the YNAB sign as the fallback so ``+/-`` literals still resolve.
        memo_amount = source_amount_milliunits_from_marker(
            transaction.memo, fallback_sign=ynab_sign
        )
        if memo_amount is None:
            return None
        return ynab_sign * abs(memo_amount)

    # 2. Zero YNAB amount, 0-amount transfer with ``+/-`` literal → ambiguous.
    if is_transfer and has_plus_minus:
        magnitude_guess = source_amount_milliunits_from_marker(
            transaction.memo, fallback_sign=1
        )
        magnitude = abs(magnitude_guess) if magnitude_guess is not None else 0
        event = AmbiguousTransfer(
            transaction_id=transaction.id,
            date=transaction.date,
            account_alias=account.alias,
            memo_amount_milliunits=magnitude,
            currency=account.currency,
        )
        ambiguous_out.append(event)
        if prompt is None:
            return None
        direction = prompt(event)
        if direction is None or direction == 0:
            return None
        sign = 1 if direction > 0 else -1
        return sign * magnitude

    # 3. Zero YNAB amount, non-transfer (or transfer without a ``+/-`` literal):
    # fall back to the sign embedded in the memo.
    return source_amount_milliunits_from_marker(
        transaction.memo, fallback_sign=None
    )


def _sentinel_updated_at(snapshot: SentinelSnapshot | None) -> datetime | None:
    """Extract the ``updated_at`` from a prior sentinel memo, if any."""
    return None
