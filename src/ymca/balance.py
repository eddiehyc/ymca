"""Local-currency balance tracking for the ``ymca sync`` pipeline.

This module layers on top of the FX conversion flow in :mod:`ymca.conversion`.
It computes per-account balance contributions for each run, detects the
in-account sentinel transaction, and produces sentinel create/update requests
plus a tolerance-check summary.

Design follows ``docs/spec.md`` §12. Core behaviors:

* **Delta mode** (``rebuild=False``): the current balance is carried forward
  from the sentinel's memo and adjusted only by new or newly-deleted cleared
  rows in the delta.
* **Rebuild mode** (``rebuild=True``): the balance is recomputed from scratch
  by parsing the FX markers (legacy + current) on every active cleared or
  reconciled transaction in the account.

The module does not make any network calls or write side effects by itself;
callers pass in the fetched transactions and the remote account snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from .memo import (
    SENTINEL_PAYEE_NAME,
    build_sentinel_memo,
    has_fx_marker,
    has_legacy_fx_marker,
    is_sentinel_payee,
    memo_marker_has_transfer_prefix,
    parse_sentinel_memo,
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
    ambiguous: list[AmbiguousTransfer] = []
    sentinel_id = prior_sentinel_txn.id if prior_sentinel_txn is not None else None

    for transaction in materialized:
        if transaction.id == sentinel_id:
            continue
        if is_sentinel_payee(transaction.payee_name):
            continue
        if transaction.id in split_skipped_ids:
            continue
        contribution = _classify_transaction(
            transaction=transaction,
            account=account,
            rebuild=rebuild,
            prompt=prompt_for_transfer_direction,
            ambiguous_out=ambiguous,
        )
        if contribution is not None:
            contributions.append(contribution)

    if rebuild:
        new_balance = sum(c.signed_source_milliunits for c in contributions)
    else:
        new_balance = prior_balance + sum(c.signed_source_milliunits for c in contributions)

    drift = compute_drift_milliunits_stronger(
        tracked_source_milliunits=new_balance,
        ynab_cleared_balance_base_milliunits=remote_account.cleared_balance_milliunits,
        rule=rule,
    )

    prev_updated_at = _sentinel_updated_at(prior_sentinel)
    new_memo = build_sentinel_memo(
        currency=account.currency,
        balance_milliunits=new_balance,
        rate_text=rule.rate_text,
        pair_label=pair_label,
        updated_at=now_utc,
        prev_balance_milliunits=(
            prior_sentinel.balance_milliunits if prior_sentinel is not None else None
        ),
        prev_updated_at=prev_updated_at,
        drift_milliunits_stronger=drift,
        stronger_currency=stronger,
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
        )
    else:
        update_request = TransactionUpdateRequest(
            transaction_id=prior_sentinel_txn.id,
            amount_milliunits=None,
            memo=new_memo,
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
    )


def _classify_transaction(
    *,
    transaction: RemoteTransaction,
    account: AccountConfig,
    rebuild: bool,
    prompt: TransferDirectionPrompt | None,
    ambiguous_out: list[AmbiguousTransfer],
) -> BalanceContribution | None:
    """Return a signed contribution for ``transaction`` (or ``None``)."""
    has_marker = has_fx_marker(transaction.memo) or has_legacy_fx_marker(transaction.memo)
    is_cleared = transaction.cleared in ("cleared", "reconciled")

    if rebuild:
        if has_marker:
            if not is_cleared or transaction.deleted:
                return None
            amount = _resolve_marked_source_milliunits(
                transaction=transaction,
                account=account,
                prompt=prompt,
                ambiguous_out=ambiguous_out,
            )
            if amount is None:
                return None
            return BalanceContribution(
                transaction_id=transaction.id,
                signed_source_milliunits=amount,
                reason="rebuild-marked",
            )
        # Unmarked in rebuild mode: if it is currently cleared/reconciled and
        # not deleted, we are about to FX-convert it and should count it.
        if not is_cleared or transaction.deleted:
            return None
        return BalanceContribution(
            transaction_id=transaction.id,
            signed_source_milliunits=transaction.amount_milliunits,
            reason="rebuild-unmarked-cleared",
        )

    # Delta mode
    if has_marker:
        if is_cleared and transaction.deleted:
            amount = _resolve_marked_source_milliunits(
                transaction=transaction,
                account=account,
                prompt=prompt,
                ambiguous_out=ambiguous_out,
            )
            if amount is None:
                return None
            return BalanceContribution(
                transaction_id=transaction.id,
                signed_source_milliunits=-amount,
                reason="existing-cleared-deleted",
            )
        # Other marked transitions are no-ops in delta mode (see §12.7 / E24).
        return None

    if transaction.deleted:
        return None
    if not is_cleared:
        return None
    return BalanceContribution(
        transaction_id=transaction.id,
        signed_source_milliunits=transaction.amount_milliunits,
        reason="new-cleared",
    )


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
    if snapshot is None:
        return None
    parsed = parse_sentinel_memo(snapshot.memo)
    if parsed is None:
        return None
    value = parsed.get("updated_at")
    if isinstance(value, datetime):
        return value
    return None
