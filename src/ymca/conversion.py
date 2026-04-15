from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from .errors import ApiError, ConfigError, UserInputError
from .memo import append_fx_marker, build_fx_marker, has_fx_marker, has_legacy_fx_marker
from .models import (
    AccountConfig,
    AccountSnapshot,
    AppState,
    ConversionOutcome,
    PlanConfig,
    PlanState,
    PreparedConversion,
    PreparedUpdate,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    ResolvedBindings,
    SkippedTransaction,
    SyncRequest,
    TransactionSnapshot,
    TransactionUpdateRequest,
)
from .state import plan_state_for, upsert_plan_state

_WHOLE_MILLIUNIT = Decimal("1")


class YnabGateway(Protocol):
    def list_plans(self, *, include_accounts: bool = False) -> tuple[RemotePlan, ...]: ...

    def list_accounts(self, plan_id: str) -> AccountSnapshot: ...

    def list_transactions_by_account(
        self,
        plan_id: str,
        account_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot: ...

    def list_transactions(
        self,
        plan_id: str,
        *,
        since_date: date | None = None,
        last_knowledge_of_server: int | None = None,
    ) -> TransactionSnapshot: ...

    def get_transaction_detail(
        self, plan_id: str, transaction_id: str
    ) -> RemoteTransactionDetail: ...

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None: ...

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None: ...


def resolve_bindings(plan: PlanConfig, gateway: YnabGateway) -> ResolvedBindings:
    remote_plans = gateway.list_plans(include_accounts=False)
    matching_plans = [remote_plan for remote_plan in remote_plans if remote_plan.name == plan.name]
    if not matching_plans:
        raise ApiError(f"Configured plan {plan.name!r} was not found in YNAB.")
    if len(matching_plans) > 1:
        raise ApiError(f"Configured plan {plan.name!r} matched multiple YNAB plans.")

    remote_plan = matching_plans[0]
    account_snapshot = gateway.list_accounts(remote_plan.id)
    accounts_by_name: dict[str, list[str]] = defaultdict(list)
    for remote_account in account_snapshot.accounts:
        if remote_account.deleted:
            continue
        accounts_by_name[remote_account.name].append(remote_account.id)

    account_ids: dict[str, str] = {}
    for account in plan.accounts:
        matches = accounts_by_name.get(account.name, [])
        if not matches:
            raise ApiError(
                "Configured account "
                f"{account.alias!r} with name {account.name!r} was not found in YNAB."
            )
        if len(matches) > 1:
            raise ApiError(
                "Configured account "
                f"{account.alias!r} with name {account.name!r} matched multiple YNAB accounts."
            )
        account_ids[account.alias] = matches[0]

    return ResolvedBindings(plan=plan, plan_id=remote_plan.id, account_ids=account_ids)


def build_prepared_conversion(
    *,
    plan: PlanConfig,
    state: AppState,
    gateway: YnabGateway,
    selected_account_aliases: Sequence[str],
    bootstrap_since: date | None,
    prompt_for_start_date: Callable[[], date],
) -> PreparedConversion:
    selected_accounts = _select_accounts(plan, selected_account_aliases)
    bindings = resolve_bindings(plan, gateway)
    sync_request = _build_sync_request(
        plan_state_for(state, plan.alias),
        bootstrap_since=bootstrap_since,
        prompt_for_start_date=prompt_for_start_date,
    )
    queried_account_ids = tuple(
        bindings.account_ids[account.alias] for account in selected_accounts
    )
    (
        fetched_items,
        fetched_transactions,
        fetched_server_knowledge,
    ) = _fetch_transactions_for_accounts(
        bindings=bindings,
        selected_accounts=selected_accounts,
        gateway=gateway,
        sync_request=sync_request,
    )

    updates: list[PreparedUpdate] = []
    skipped: list[SkippedTransaction] = []
    candidate_items: list[tuple[AccountConfig, RemoteTransaction]] = []
    for account, transaction in fetched_items:
        skip_reason = _summary_skip_reason(transaction)
        if skip_reason is None:
            candidate_items.append((account, transaction))
            continue
        skipped.append(
            SkippedTransaction(
                transaction_id=transaction.id,
                date=transaction.date,
                account_alias=account.alias,
                reason=skip_reason,
            )
        )

    deduped_items, transfer_skips = _dedupe_transfer_transactions(candidate_items)
    skipped.extend(transfer_skips)

    for account, transaction in deduped_items:
        detail = gateway.get_transaction_detail(bindings.plan_id, transaction.id)
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

        updates.append(_prepare_update(plan, account, detail))

    return PreparedConversion(
        bindings=bindings,
        sync_request=sync_request,
        queried_account_ids=queried_account_ids,
        fetched_transactions=fetched_transactions,
        fetched_server_knowledge=fetched_server_knowledge,
        updates=tuple(updates),
        skipped=tuple(skipped),
    )


def execute_conversion(
    *,
    prepared: PreparedConversion,
    state: AppState,
    gateway: YnabGateway,
    apply_updates: bool,
) -> ConversionOutcome:
    if not apply_updates:
        return ConversionOutcome(
            prepared=prepared,
            applied=False,
            writes_performed=0,
            saved_server_knowledge=None,
            new_state=state,
        )

    for requests in _group_update_requests_by_account(prepared.updates).values():
        gateway.update_transactions(prepared.bindings.plan_id, requests)

    saved_server_knowledge = prepared.fetched_server_knowledge
    if prepared.updates:
        saved_server_knowledge = _refresh_server_knowledge_for_accounts(
            gateway=gateway,
            plan_id=prepared.bindings.plan_id,
            account_ids=prepared.queried_account_ids,
            last_knowledge_of_server=prepared.fetched_server_knowledge,
        )

    next_state = upsert_plan_state(
        state,
        alias=prepared.bindings.plan.alias,
        plan_id=prepared.bindings.plan_id,
        account_ids=prepared.bindings.account_ids,
        server_knowledge=saved_server_knowledge,
    )

    return ConversionOutcome(
        prepared=prepared,
        applied=True,
        writes_performed=len(prepared.updates),
        saved_server_knowledge=saved_server_knowledge,
        new_state=next_state,
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


def _build_sync_request(
    plan_state: PlanState | None,
    *,
    bootstrap_since: date | None,
    prompt_for_start_date: Callable[[], date],
) -> SyncRequest:
    if plan_state is not None and plan_state.server_knowledge is not None:
        return SyncRequest(
            last_knowledge_of_server=plan_state.server_knowledge,
            since_date=None,
            used_bootstrap=False,
        )

    since_date = bootstrap_since if bootstrap_since is not None else prompt_for_start_date()
    return SyncRequest(
        last_knowledge_of_server=None,
        since_date=since_date,
        used_bootstrap=True,
    )


def _summary_skip_reason(transaction: RemoteTransaction | RemoteTransactionDetail) -> str | None:
    if transaction.deleted:
        return "deleted"
    if has_fx_marker(transaction.memo):
        return "already-converted"
    if has_legacy_fx_marker(transaction.memo):
        return "legacy-marker"
    return None


def _detail_skip_reason(transaction: RemoteTransactionDetail) -> str | None:
    summary_skip_reason = _summary_skip_reason(transaction)
    if summary_skip_reason is not None:
        return summary_skip_reason
    if transaction.subtransaction_count > 0:
        return "split"
    if transaction.amount_milliunits == 0:
        return "zero-amount"
    return None


def _prepare_update(
    plan: PlanConfig,
    account: AccountConfig,
    transaction: RemoteTransactionDetail,
) -> PreparedUpdate:
    is_transfer = transaction.transfer_account_id is not None
    fx_rule = plan.fx_rates[account.currency]
    pair_label = fx_rule.pair_label(
        base_currency=plan.base_currency,
        source_currency=account.currency,
    )
    new_amount_milliunits = _convert_amount_milliunits(
        transaction.amount_milliunits,
        divide_to_base=fx_rule.divide_to_base,
        rate=fx_rule.rate,
    )
    marker = build_fx_marker(
        source_amount_milliunits=transaction.amount_milliunits,
        source_currency=account.currency,
        rate_text=fx_rule.rate_text,
        pair_label=pair_label,
        always_show_sign=is_transfer,
    )
    new_memo = append_fx_marker(transaction.memo, marker)
    request = TransactionUpdateRequest(
        transaction_id=transaction.id,
        amount_milliunits=new_amount_milliunits,
        memo=new_memo,
    )
    return PreparedUpdate(
        transaction_id=transaction.id,
        date=transaction.date,
        account_alias=account.alias,
        account_name=account.name,
        is_transfer=is_transfer,
        source_currency=account.currency,
        source_amount_milliunits=transaction.amount_milliunits,
        converted_currency=plan.base_currency,
        converted_amount_milliunits=new_amount_milliunits,
        rate_text=fx_rule.rate_text,
        pair_label=pair_label,
        old_memo=transaction.memo,
        new_memo=new_memo,
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
    rounded = converted.quantize(_WHOLE_MILLIUNIT, rounding=ROUND_HALF_UP)
    return int(rounded)


def _fetch_transactions_for_accounts(
    *,
    bindings: ResolvedBindings,
    selected_accounts: tuple[AccountConfig, ...],
    gateway: YnabGateway,
    sync_request: SyncRequest,
) -> tuple[list[tuple[AccountConfig, RemoteTransaction]], int, int]:
    fetched_items: list[tuple[AccountConfig, RemoteTransaction]] = []
    fetched_transactions = 0
    fetched_server_knowledge = sync_request.last_knowledge_of_server or 0

    for account in selected_accounts:
        snapshot = gateway.list_transactions_by_account(
            bindings.plan_id,
            bindings.account_ids[account.alias],
            since_date=sync_request.since_date,
            last_knowledge_of_server=sync_request.last_knowledge_of_server,
        )
        fetched_transactions += len(snapshot.transactions)
        fetched_server_knowledge = max(fetched_server_knowledge, snapshot.server_knowledge)
        for transaction in snapshot.transactions:
            fetched_items.append((account, transaction))

    fetched_items.sort(key=lambda item: (item[1].date, item[1].id))
    return fetched_items, fetched_transactions, fetched_server_knowledge


def _group_update_requests_by_account(
    updates: Sequence[PreparedUpdate],
) -> dict[str, tuple[TransactionUpdateRequest, ...]]:
    grouped_requests: dict[str, list[TransactionUpdateRequest]] = defaultdict(list)
    for update in updates:
        grouped_requests[update.account_alias].append(update.request)
    return {
        account_alias: tuple(requests)
        for account_alias, requests in grouped_requests.items()
    }


def _dedupe_transfer_transactions(
    items: list[tuple[AccountConfig, RemoteTransaction]],
) -> tuple[list[tuple[AccountConfig, RemoteTransaction]], list[SkippedTransaction]]:
    selected: list[tuple[AccountConfig, RemoteTransaction]] = []
    skipped: list[SkippedTransaction] = []
    transfer_groups: dict[
        tuple[str, str], list[tuple[AccountConfig, RemoteTransaction]]
    ] = defaultdict(list)

    for account, transaction in items:
        if transaction.transfer_transaction_id is None:
            selected.append((account, transaction))
            continue
        transfer_groups[_transfer_group_key(transaction)].append((account, transaction))

    for group_items in transfer_groups.values():
        if len(group_items) == 1:
            selected.append(group_items[0])
            continue

        chosen = min(group_items, key=_transfer_preference_key)
        selected.append(chosen)
        for account, transaction in group_items:
            if (account, transaction) == chosen:
                continue
            skipped.append(
                SkippedTransaction(
                    transaction_id=transaction.id,
                    date=transaction.date,
                    account_alias=account.alias,
                    reason="paired-transfer",
                )
            )

    selected.sort(key=lambda item: (item[1].date, item[1].id))
    skipped.sort(key=lambda item: (item.date, item.transaction_id))
    return selected, skipped


def _transfer_group_key(transaction: RemoteTransaction) -> tuple[str, str]:
    if transaction.transfer_transaction_id is None:
        return (transaction.id, transaction.id)
    first, second = sorted((transaction.id, transaction.transfer_transaction_id))
    return first, second


def _transfer_preference_key(
    item: tuple[AccountConfig, RemoteTransaction],
) -> tuple[int, date, str, str]:
    account, transaction = item
    return (
        0 if transaction.amount_milliunits < 0 else 1,
        transaction.date,
        account.alias,
        transaction.id,
    )


def _refresh_server_knowledge_for_accounts(
    *,
    gateway: YnabGateway,
    plan_id: str,
    account_ids: tuple[str, ...],
    last_knowledge_of_server: int,
) -> int:
    refreshed_knowledge = last_knowledge_of_server
    for account_id in account_ids:
        snapshot = gateway.list_transactions_by_account(
            plan_id,
            account_id,
            last_knowledge_of_server=last_knowledge_of_server,
        )
        refreshed_knowledge = max(refreshed_knowledge, snapshot.server_knowledge)
    return refreshed_knowledge
