"""Live-API coverage for workflow W3: ``ymca discover``.

Asserts that :meth:`CountingYnabClient.list_plans` sees the dedicated
``_Intergration Test_ USE ONLY`` plan and returns its open accounts. Does not
write anything.

API cost: 1 call (``get_plans(include_accounts=True)``).
"""

from __future__ import annotations

import pytest

from .conftest import IntegrationEnvironment
from .helpers import INTEGRATION_PLAN_NAME, resolve_integration_accounts


@pytest.mark.integration
def test_discover_lists_integration_plan_with_expected_accounts(
    integration_env: IntegrationEnvironment,
) -> None:
    plans = integration_env.gateway.list_plans(include_accounts=True)

    matching = [plan for plan in plans if plan.name == INTEGRATION_PLAN_NAME]
    assert len(matching) == 1, (
        f"Expected exactly one plan named {INTEGRATION_PLAN_NAME!r}; "
        f"got {len(matching)}."
    )
    plan = matching[0]

    open_accounts = tuple(
        account for account in plan.accounts if not account.deleted and not account.closed
    )
    assert open_accounts, "Integration plan returned no open accounts via discover."

    resolved = resolve_integration_accounts(open_accounts)
    assert resolved.hkd_primary.id == integration_env.accounts_by_name()[
        resolved.hkd_primary.name
    ].id
    assert resolved.gbp.id == integration_env.accounts_by_name()[resolved.gbp.name].id
