from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .conversion import YnabGateway, resolve_bindings
from .errors import ConfigError, UserInputError
from .models import AccountConfig, PlanConfig, RemoteTransaction, ResolvedBindings


@dataclass(frozen=True, slots=True)
class AccountDeltaResult:
    account_alias: str
    account_name: str
    returned_server_knowledge: int
    transactions: tuple[RemoteTransaction, ...]


@dataclass(frozen=True, slots=True)
class AccountDeltaReport:
    bindings: ResolvedBindings
    requested_last_knowledge_of_server: int
    returned_server_knowledge: int
    fetched_transactions: int
    account_results: tuple[AccountDeltaResult, ...]


def build_account_delta_report(
    *,
    plan: PlanConfig,
    gateway: YnabGateway,
    selected_account_aliases: Sequence[str],
    last_knowledge_of_server: int,
    bindings: ResolvedBindings | None = None,
) -> AccountDeltaReport:
    selected_accounts = _select_accounts(plan, selected_account_aliases)
    resolved_bindings = bindings if bindings is not None else resolve_bindings(plan, gateway)

    fetched_transactions = 0
    returned_server_knowledge = last_knowledge_of_server
    account_results: list[AccountDeltaResult] = []

    for account in selected_accounts:
        snapshot = gateway.list_transactions_by_account(
            resolved_bindings.plan_id,
            resolved_bindings.account_ids[account.alias],
            last_knowledge_of_server=last_knowledge_of_server,
        )
        transactions = tuple(sorted(snapshot.transactions, key=lambda item: (item.date, item.id)))
        fetched_transactions += len(transactions)
        returned_server_knowledge = max(returned_server_knowledge, snapshot.server_knowledge)
        account_results.append(
            AccountDeltaResult(
                account_alias=account.alias,
                account_name=account.name,
                returned_server_knowledge=snapshot.server_knowledge,
                transactions=transactions,
            )
        )

    return AccountDeltaReport(
        bindings=resolved_bindings,
        requested_last_knowledge_of_server=last_knowledge_of_server,
        returned_server_knowledge=returned_server_knowledge,
        fetched_transactions=fetched_transactions,
        account_results=tuple(account_results),
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
