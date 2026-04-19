from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

from .balance import TransferDirectionPrompt, build_tracking_update
from .errors import ApiError, ConfigError, UserInputError
from .memo import (
    append_fx_marker,
    build_fx_marker,
    has_fx_marker,
    has_legacy_fx_marker,
    is_sentinel_payee,
)
from .models import (
    AccountConfig,
    AccountSnapshot,
    AppState,
    ConversionOutcome,
    NewTransactionRequest,
    PlanConfig,
    PlanState,
    PreparedConversion,
    PreparedTrackingUpdate,
    PreparedUpdate,
    RemoteAccount,
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

    def get_transaction_detail(
        self, plan_id: str, transaction_id: str
    ) -> RemoteTransactionDetail: ...

    def update_transaction(self, plan_id: str, request: TransactionUpdateRequest) -> None: ...

    def update_transactions(
        self, plan_id: str, requests: Sequence[TransactionUpdateRequest]
    ) -> None: ...

    def create_transaction(self, plan_id: str, request: NewTransactionRequest) -> str: ...

    def delete_transaction(self, plan_id: str, transaction_id: str) -> None: ...


def resolve_bindings(plan: PlanConfig, gateway: YnabGateway) -> ResolvedBindings:
    remote_plans = gateway.list_plans(include_accounts=False)
    matching_plans = [remote_plan for remote_plan in remote_plans if remote_plan.name == plan.name]
    if not matching_plans:
        raise ApiError(f"Configured plan {plan.name!r} was not found in YNAB.")
    if len(matching_plans) > 1:
        raise ApiError(f"Configured plan {plan.name!r} matched multiple YNAB plans.")

    remote_plan = matching_plans[0]
    account_snapshot = gateway.list_accounts(remote_plan.id)
    accounts_by_name: dict[str, list[RemoteAccount]] = defaultdict(list)
    for remote_account in account_snapshot.accounts:
        if remote_account.deleted:
            continue
        accounts_by_name[remote_account.name].append(remote_account)

    account_ids: dict[str, str] = {}
    remote_accounts_by_alias: dict[str, RemoteAccount] = {}
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
        account_ids[account.alias] = matches[0].id
        remote_accounts_by_alias[account.alias] = matches[0]

    return ResolvedBindings(
        plan=plan,
        plan_id=remote_plan.id,
        account_ids=account_ids,
        remote_accounts_by_alias=remote_accounts_by_alias,
    )


def build_prepared_conversion(
    *,
    plan: PlanConfig,
    state: AppState,
    gateway: YnabGateway,
    selected_account_aliases: Sequence[str],
    bootstrap_since: date | None,
    prompt_for_start_date: Callable[[], date],
    rebuild_balance: bool = False,
    prompt_for_transfer_direction: TransferDirectionPrompt | None = None,
    now_utc: datetime | None = None,
) -> PreparedConversion:
    selected_accounts = _select_accounts(plan, selected_account_aliases)
    if rebuild_balance and not any(a.track_local_balance for a in selected_accounts):
        raise UserInputError(
            "--rebuild-balance requires at least one account in scope with "
            "track_local_balance: true."
        )
    bindings = resolve_bindings(plan, gateway)
    if rebuild_balance:
        sync_request = SyncRequest(
            last_knowledge_of_server=None,
            since_date=None,
            used_bootstrap=True,
        )
    else:
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
        if is_sentinel_payee(transaction.payee_name):
            skipped.append(
                SkippedTransaction(
                    transaction_id=transaction.id,
                    date=transaction.date,
                    account_alias=account.alias,
                    reason="sentinel",
                )
            )
            continue
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

    split_skipped_ids = {
        entry.transaction_id for entry in skipped if entry.reason == "split"
    }
    prior_plan_state = plan_state_for(state, plan.alias)
    saved_sentinel_ids: Mapping[str, str] = (
        dict(prior_plan_state.sentinel_ids) if prior_plan_state is not None else {}
    )
    tracking = _build_tracking_updates(
        plan=plan,
        bindings=bindings,
        selected_accounts=selected_accounts,
        fetched_items=fetched_items,
        split_skipped_ids=split_skipped_ids,
        rebuild=rebuild_balance,
        now_utc=now_utc or datetime.now(UTC),
        prompt_for_transfer_direction=prompt_for_transfer_direction,
        gateway=gateway,
        saved_sentinel_ids=saved_sentinel_ids,
    )

    return PreparedConversion(
        bindings=bindings,
        sync_request=sync_request,
        queried_account_ids=queried_account_ids,
        fetched_transactions=fetched_transactions,
        fetched_server_knowledge=fetched_server_knowledge,
        updates=tuple(updates),
        skipped=tuple(skipped),
        tracking=tracking,
        rebuild_balance=rebuild_balance,
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

    sentinel_writes, sentinels_created, written_sentinel_ids = _apply_tracking_writes(
        gateway=gateway,
        plan_id=prepared.bindings.plan_id,
        tracking=prepared.tracking,
    )

    saved_server_knowledge = prepared.fetched_server_knowledge
    if prepared.updates or sentinel_writes:
        saved_server_knowledge = _refresh_server_knowledge_for_accounts(
            gateway=gateway,
            plan_id=prepared.bindings.plan_id,
            account_ids=prepared.queried_account_ids,
            last_knowledge_of_server=prepared.fetched_server_knowledge,
        )

    # Merge the freshly-written sentinel ids on top of the ids we had saved
    # for this plan, so untouched tracked accounts retain their stored id.
    prior_plan_state = plan_state_for(state, prepared.bindings.plan.alias)
    merged_sentinel_ids: dict[str, str] = (
        dict(prior_plan_state.sentinel_ids) if prior_plan_state is not None else {}
    )
    merged_sentinel_ids.update(written_sentinel_ids)

    next_state = upsert_plan_state(
        state,
        alias=prepared.bindings.plan.alias,
        plan_id=prepared.bindings.plan_id,
        account_ids=prepared.bindings.account_ids,
        server_knowledge=saved_server_knowledge,
        sentinel_ids=merged_sentinel_ids,
    )

    return ConversionOutcome(
        prepared=prepared,
        applied=True,
        writes_performed=len(prepared.updates),
        saved_server_knowledge=saved_server_knowledge,
        new_state=next_state,
        sentinel_writes=sentinel_writes,
        sentinels_created=sentinels_created,
    )


def _build_tracking_updates(
    *,
    plan: PlanConfig,
    bindings: ResolvedBindings,
    selected_accounts: tuple[AccountConfig, ...],
    fetched_items: Sequence[tuple[AccountConfig, RemoteTransaction]],
    split_skipped_ids: set[str],
    rebuild: bool,
    now_utc: datetime,
    prompt_for_transfer_direction: TransferDirectionPrompt | None,
    gateway: YnabGateway,
    saved_sentinel_ids: Mapping[str, str],
) -> tuple[PreparedTrackingUpdate, ...]:
    tracked_accounts = [a for a in selected_accounts if a.track_local_balance]
    if not tracked_accounts:
        return ()

    grouped: dict[str, list[RemoteTransaction]] = defaultdict(list)
    for account, transaction in fetched_items:
        grouped[account.alias].append(transaction)

    prepared: list[PreparedTrackingUpdate] = []
    for account in tracked_accounts:
        account_id = bindings.account_ids[account.alias]
        remote_account = bindings.remote_accounts_by_alias.get(account.alias)
        if remote_account is None:
            raise ApiError(
                f"Missing remote account snapshot for tracked account {account.alias!r}."
            )

        transactions = list(grouped.get(account.alias, []))
        saved_id = saved_sentinel_ids.get(account.alias)
        if saved_id is not None and not any(t.id == saved_id for t in transactions):
            # The sentinel only appears in the delta when WE just changed it.
            # On a quiet run the delta is empty and scanning ``transactions``
            # would miss a sentinel that already exists in YNAB. Fetch it
            # directly so ``build_tracking_update`` can pick it up as the
            # prior state.
            fetched_sentinel = _fetch_saved_sentinel(
                gateway=gateway, plan_id=bindings.plan_id, sentinel_id=saved_id
            )
            if fetched_sentinel is not None:
                transactions.append(fetched_sentinel)

        prepared.append(
            build_tracking_update(
                plan=plan,
                account=account,
                account_id=account_id,
                remote_account=remote_account,
                transactions=transactions,
                split_skipped_ids=split_skipped_ids,
                rebuild=rebuild,
                now_utc=now_utc,
                prompt_for_transfer_direction=prompt_for_transfer_direction,
            )
        )
    return tuple(prepared)


def _fetch_saved_sentinel(
    *, gateway: YnabGateway, plan_id: str, sentinel_id: str
) -> RemoteTransaction | None:
    """Fetch a previously-known sentinel transaction by id.

    Returns ``None`` when the sentinel has been deleted, re-tagged with a
    different payee, or otherwise can no longer be found. In that case
    ``build_tracking_update`` falls through to creating a fresh sentinel.
    """
    try:
        detail = gateway.get_transaction_detail(plan_id, sentinel_id)
    except ApiError:
        return None
    if detail.deleted:
        return None
    if not is_sentinel_payee(detail.payee_name):
        return None
    return RemoteTransaction(
        id=detail.id,
        date=detail.date,
        amount_milliunits=detail.amount_milliunits,
        memo=detail.memo,
        account_id=detail.account_id,
        transfer_account_id=detail.transfer_account_id,
        transfer_transaction_id=detail.transfer_transaction_id,
        deleted=detail.deleted,
        payee_name=detail.payee_name,
        cleared=detail.cleared,
    )


def _apply_tracking_writes(
    *,
    gateway: YnabGateway,
    plan_id: str,
    tracking: Sequence[PreparedTrackingUpdate],
) -> tuple[int, int, dict[str, str]]:
    """Apply sentinel create/update calls and return captured ids by alias."""
    total_writes = 0
    created = 0
    new_ids: dict[str, str] = {}
    for entry in tracking:
        if entry.create_sentinel is not None:
            new_id = gateway.create_transaction(plan_id, entry.create_sentinel)
            new_ids[entry.account_alias] = new_id
            total_writes += 1
            created += 1
            continue
        if entry.update_sentinel is not None:
            gateway.update_transaction(plan_id, entry.update_sentinel)
            new_ids[entry.account_alias] = entry.update_sentinel.transaction_id
            total_writes += 1
    return total_writes, created, new_ids


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
    if bootstrap_since is not None:
        return SyncRequest(
            last_knowledge_of_server=None,
            since_date=bootstrap_since,
            used_bootstrap=True,
        )

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
        transfer_prefix=is_transfer,
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
