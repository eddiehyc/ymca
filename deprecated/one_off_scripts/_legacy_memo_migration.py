from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from ._shared import (
    AccountConfig,
    ConfigError,
    PlanConfig,
    RemoteTransactionDetail,
    ResolvedBindings,
    SkippedTransaction,
    TransactionUpdateRequest,
    UserInputError,
    YnabGateway,
    has_fx_marker,
    has_legacy_fx_marker,
    replace_legacy_fx_marker,
    resolve_bindings,
)


@dataclass(frozen=True, slots=True)
class LegacyMemoMigrationUpdate:
    transaction_id: str
    date: date
    account_alias: str
    old_memo: str
    new_memo: str
    use_single_update: bool
    request: TransactionUpdateRequest


@dataclass(frozen=True, slots=True)
class LegacyMemoMigrationPlan:
    bindings: ResolvedBindings
    scanned_transactions: int
    updates: tuple[LegacyMemoMigrationUpdate, ...]
    skipped: tuple[SkippedTransaction, ...]


def build_legacy_memo_migration_plan(
    *,
    plan: PlanConfig,
    gateway: YnabGateway,
    selected_account_aliases: Sequence[str],
    bindings: ResolvedBindings | None = None,
) -> LegacyMemoMigrationPlan:
    selected_accounts = _select_accounts(plan, selected_account_aliases)
    resolved_bindings = bindings if bindings is not None else resolve_bindings(plan, gateway)
    pair_labels = {
        currency: f"{plan.base_currency}/{currency}" for currency in plan.fx_rates
    }

    scanned_transactions = 0
    updates: list[LegacyMemoMigrationUpdate] = []
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
            if has_fx_marker(transaction.memo):
                continue
            if not has_legacy_fx_marker(transaction.memo):
                continue

            new_memo = replace_legacy_fx_marker(
                transaction.memo,
                pair_label_for_currency=pair_labels,
                transfer=transaction.transfer_account_id is not None,
            )
            if new_memo is None:
                skipped.append(
                    SkippedTransaction(
                        transaction_id=transaction.id,
                        date=transaction.date,
                        account_alias=account.alias,
                        reason="unconfigured-legacy-marker",
                    )
                )
                continue

            detail = gateway.get_transaction_detail(resolved_bindings.plan_id, transaction.id)
            detail_skip_reason = _detail_skip_reason(detail)
            if detail_skip_reason is not None:
                skipped.append(
                    SkippedTransaction(
                        transaction_id=detail.id,
                        date=detail.date,
                        account_alias=account.alias,
                        reason=detail_skip_reason,
                    )
                )
                continue

            updates.append(
                LegacyMemoMigrationUpdate(
                    transaction_id=transaction.id,
                    date=transaction.date,
                    account_alias=account.alias,
                    old_memo=transaction.memo,
                    new_memo=new_memo,
                    use_single_update=detail.subtransaction_count > 0,
                    request=_build_update_request(detail=detail, new_memo=new_memo),
                )
            )

    return LegacyMemoMigrationPlan(
        bindings=resolved_bindings,
        scanned_transactions=scanned_transactions,
        updates=tuple(updates),
        skipped=tuple(skipped),
    )


def apply_legacy_memo_migration_plan(
    *,
    gateway: YnabGateway,
    plan: LegacyMemoMigrationPlan,
) -> int:
    grouped_requests: dict[str, list[TransactionUpdateRequest]] = defaultdict(list)
    for update in plan.updates:
        if update.use_single_update:
            gateway.update_transaction(plan.bindings.plan_id, update.request)
            continue
        grouped_requests[update.account_alias].append(update.request)

    for requests in grouped_requests.values():
        gateway.update_transactions(plan.bindings.plan_id, tuple(requests))
    return len(plan.updates)


def _detail_skip_reason(transaction: RemoteTransactionDetail) -> str | None:
    if transaction.deleted:
        return "deleted"
    return None


def _build_update_request(
    *,
    detail: RemoteTransactionDetail,
    new_memo: str,
) -> TransactionUpdateRequest:
    if detail.subtransaction_count == 0:
        return TransactionUpdateRequest(
            transaction_id=detail.id,
            amount_milliunits=None,
            memo=new_memo,
        )

    return TransactionUpdateRequest(
        transaction_id=detail.id,
        amount_milliunits=detail.amount_milliunits,
        memo=new_memo,
        flag_color=detail.flag_color,
        account_id=detail.account_id,
        date=detail.date,
        payee_id=detail.payee_id,
        payee_name=detail.payee_name,
        category_id=detail.category_id,
        cleared=detail.cleared,
        approved=detail.approved,
        subtransactions=detail.subtransactions,
    )


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
