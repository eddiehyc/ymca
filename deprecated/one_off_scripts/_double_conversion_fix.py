from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ._shared import (
    FX_MARKER_RE,
    LEGACY_FX_MARKER_RE,
    AccountConfig,
    ConfigError,
    FxRule,
    PlanConfig,
    RemoteTransaction,
    ResolvedBindings,
    SkippedTransaction,
    TransactionUpdateRequest,
    UserInputError,
    YnabGateway,
    amount_text_to_milliunits,
    has_fx_marker,
    resolve_bindings,
)

_SEPARATOR_PATTERN = r"(?:\||·)"
_WHOLE_MILLIUNIT = Decimal("1")
_ROUNDING_TOLERANCE_MILLIUNITS = 5


@dataclass(frozen=True, slots=True)
class DoubleConversionFixUpdate:
    transaction_id: str
    date: date
    account_alias: str
    old_amount_milliunits: int
    new_amount_milliunits: int
    old_memo: str
    new_memo: str
    request: TransactionUpdateRequest


@dataclass(frozen=True, slots=True)
class DoubleConversionFixPlan:
    bindings: ResolvedBindings
    scanned_transactions: int
    updates: tuple[DoubleConversionFixUpdate, ...]
    skipped: tuple[SkippedTransaction, ...]


def build_double_conversion_fix_plan(
    *,
    plan: PlanConfig,
    gateway: YnabGateway,
    selected_account_aliases: Sequence[str],
    bindings: ResolvedBindings | None = None,
) -> DoubleConversionFixPlan:
    selected_accounts = _select_accounts(plan, selected_account_aliases)
    resolved_bindings = bindings if bindings is not None else resolve_bindings(plan, gateway)

    scanned_transactions = 0
    updates: list[DoubleConversionFixUpdate] = []
    skipped: list[SkippedTransaction] = []

    for account in selected_accounts:
        snapshot = gateway.list_transactions_by_account(
            resolved_bindings.plan_id,
            resolved_bindings.account_ids[account.alias],
        )
        scanned_transactions += len(snapshot.transactions)
        for transaction in sorted(snapshot.transactions, key=lambda item: (item.date, item.id)):
            if transaction.deleted:
                skipped.append(
                    SkippedTransaction(
                        transaction_id=transaction.id,
                        date=transaction.date,
                        account_alias=account.alias,
                        reason="deleted",
                    )
                )
                continue
            if transaction.memo is None:
                continue
            prepared_update = _prepare_fix_update(
                account=account,
                fx_rule=plan.fx_rates[account.currency],
                transaction=transaction,
            )
            if prepared_update is None:
                continue
            updates.append(prepared_update)

    return DoubleConversionFixPlan(
        bindings=resolved_bindings,
        scanned_transactions=scanned_transactions,
        updates=tuple(updates),
        skipped=tuple(skipped),
    )


def apply_double_conversion_fix_plan(
    *,
    gateway: YnabGateway,
    plan: DoubleConversionFixPlan,
) -> int:
    grouped_requests: dict[str, list[TransactionUpdateRequest]] = defaultdict(list)
    for update in plan.updates:
        grouped_requests[update.account_alias].append(update.request)

    for requests in grouped_requests.values():
        gateway.update_transactions(plan.bindings.plan_id, tuple(requests))
    return len(plan.updates)


def _prepare_fix_update(
    *,
    account: AccountConfig,
    fx_rule: FxRule,
    transaction: RemoteTransaction,
) -> DoubleConversionFixUpdate | None:
    memo = transaction.memo
    if memo is None:
        return None

    legacy_match = LEGACY_FX_MARKER_RE.search(memo)
    fx_match = FX_MARKER_RE.search(memo)
    if legacy_match is None or fx_match is None:
        return None
    if legacy_match.group("currency") != account.currency:
        return None
    if fx_match.group("currency") != account.currency:
        return None

    corrected_amount_milliunits = amount_text_to_milliunits(
        legacy_match.group("amount"),
        fallback_sign=transaction.amount_milliunits,
    )
    once_converted_from_fx_amount = amount_text_to_milliunits(
        fx_match.group("amount"),
        fallback_sign=transaction.amount_milliunits,
    )
    expected_corrected_amount = _convert_amount_milliunits(
        once_converted_from_fx_amount,
        divide_to_base=fx_rule.divide_to_base,
        rate=fx_rule.rate,
    )
    if not _amounts_close(
        corrected_amount_milliunits,
        expected_corrected_amount,
        tolerance_milliunits=_ROUNDING_TOLERANCE_MILLIUNITS,
    ):
        return None

    expected_current_amount = _convert_amount_milliunits(
        corrected_amount_milliunits,
        divide_to_base=fx_rule.divide_to_base,
        rate=fx_rule.rate,
    )
    expected_current_amount_from_fx = _convert_amount_milliunits(
        expected_corrected_amount,
        divide_to_base=fx_rule.divide_to_base,
        rate=fx_rule.rate,
    )
    if not (
        _amounts_close(
            transaction.amount_milliunits,
            expected_current_amount,
            tolerance_milliunits=_ROUNDING_TOLERANCE_MILLIUNITS,
        )
        or _amounts_close(
            transaction.amount_milliunits,
            expected_current_amount_from_fx,
            tolerance_milliunits=_ROUNDING_TOLERANCE_MILLIUNITS,
        )
    ):
        return None

    corrected_memo = _remove_legacy_marker(memo, legacy_match)
    if not corrected_memo or not has_fx_marker(corrected_memo):
        return None

    request = TransactionUpdateRequest(
        transaction_id=transaction.id,
        amount_milliunits=corrected_amount_milliunits,
        memo=corrected_memo,
    )
    return DoubleConversionFixUpdate(
        transaction_id=transaction.id,
        date=transaction.date,
        account_alias=account.alias,
        old_amount_milliunits=transaction.amount_milliunits,
        new_amount_milliunits=corrected_amount_milliunits,
        old_memo=memo,
        new_memo=corrected_memo,
        request=request,
    )


def _convert_amount_milliunits(
    amount_milliunits: int,
    *,
    divide_to_base: bool,
    rate: Decimal,
) -> int:
    source_amount = Decimal(amount_milliunits)
    if divide_to_base:
        converted = source_amount / rate
    else:
        converted = source_amount * rate
    rounded = converted.quantize(
        _WHOLE_MILLIUNIT,
        rounding=ROUND_HALF_UP,
    )
    return int(rounded)


def _remove_legacy_marker(memo: str, legacy_match: re.Match[str]) -> str:
    before = _trim_separator_suffix(memo[: legacy_match.start()])
    after = _trim_separator_prefix(memo[legacy_match.end() :])
    parts = [part for part in (before, after) if part]
    return " | ".join(parts)


def _trim_separator_suffix(text: str) -> str:
    return re.sub(rf"(?:\s*{_SEPARATOR_PATTERN}\s*)+$", "", text).strip()


def _trim_separator_prefix(text: str) -> str:
    return re.sub(rf"^(?:\s*{_SEPARATOR_PATTERN}\s*)+", "", text).strip()


def _amounts_close(left: int, right: int, *, tolerance_milliunits: int) -> bool:
    return abs(left - right) <= tolerance_milliunits


def _select_accounts(
    plan: PlanConfig,
    selected_account_aliases: Sequence[str],
) -> tuple[AccountConfig, ...]:
    configured_accounts = {account.alias: account for account in plan.accounts if account.enabled}
    if not configured_accounts:
        raise ConfigError("No enabled accounts found in config.")

    if not selected_account_aliases:
        return tuple(configured_accounts.values())

    missing = [alias for alias in selected_account_aliases if alias not in configured_accounts]
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise UserInputError(f"Unknown or disabled account alias: {missing_text}.")

    return tuple(configured_accounts[alias] for alias in selected_account_aliases)
