"""Live-API coverage for workflow W2: ``ymca config check``.

Resolves bindings for a programmatically-built :class:`PlanConfig` that
mirrors the expected layout of ``_Intergration Test_ USE ONLY``, confirming
that :func:`ymca.conversion.resolve_bindings` finds the plan and every
configured account with a single live API round-trip per YNAB endpoint.

API cost: 2 calls (``list_plans`` + ``list_accounts``).
"""

from __future__ import annotations

import pytest

from ymca.conversion import resolve_bindings

from .conftest import IntegrationEnvironment
from .helpers import (
    build_plan_config,
    resolve_integration_accounts,
)


@pytest.mark.integration
def test_config_check_resolves_every_configured_account(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = build_plan_config(integration_env.plan.name, account_plan)

    bindings = resolve_bindings(plan_config, integration_env.gateway)

    assert bindings.plan_id == integration_env.plan.id
    configured_aliases = {account.alias for account in plan_config.accounts}
    assert set(bindings.account_ids) == configured_aliases
    accounts_by_name = integration_env.accounts_by_name()
    for account in plan_config.accounts:
        assert bindings.account_ids[account.alias] == accounts_by_name[account.name].id
