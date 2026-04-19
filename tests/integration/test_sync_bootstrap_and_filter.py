"""Live-API coverage for workflows W6 and W7.

* ``ymca sync --bootstrap-since``: asserts the prepared conversion's
  :class:`SyncRequest` reflects the bootstrap override (``used_bootstrap=True``,
  no ``last_knowledge_of_server``).
* ``ymca sync --account``: asserts only the selected account ids show up
  in :attr:`PreparedConversion.queried_account_ids`.

Both tests do dry-runs only (no writes, no cleanup per-test cost beyond the
shared session finalizer).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ymca.conversion import build_prepared_conversion

from .conftest import IntegrationEnvironment
from .helpers import (
    build_plan_config,
    empty_app_state,
    resolve_integration_accounts,
)


def _prompt_never_called() -> date:
    pytest.fail("bootstrap-since tests must not reach the start-date prompt.")


@pytest.mark.integration
def test_sync_bootstrap_since_overrides_saved_state(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = build_plan_config(integration_env.plan.name, account_plan)
    bootstrap_since = date.today() - timedelta(days=7)

    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=integration_env.gateway,
        selected_account_aliases=(),
        bootstrap_since=bootstrap_since,
        prompt_for_start_date=_prompt_never_called,
    )

    assert prepared.sync_request.used_bootstrap is True
    assert prepared.sync_request.since_date == bootstrap_since
    assert prepared.sync_request.last_knowledge_of_server is None


@pytest.mark.integration
def test_sync_account_filter_limits_queried_accounts(
    integration_env: IntegrationEnvironment,
) -> None:
    account_plan = resolve_integration_accounts(integration_env.accounts)
    plan_config = build_plan_config(integration_env.plan.name, account_plan)

    prepared = build_prepared_conversion(
        plan=plan_config,
        state=empty_app_state(),
        gateway=integration_env.gateway,
        selected_account_aliases=("gbp_main",),
        bootstrap_since=date.today() - timedelta(days=1),
        prompt_for_start_date=_prompt_never_called,
    )

    assert prepared.queried_account_ids == (account_plan.gbp.id,), (
        "Account filter must narrow queried_account_ids to exactly the "
        "selected alias' YNAB id."
    )
