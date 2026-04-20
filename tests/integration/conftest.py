"""Session-scoped fixtures shared by every integration test.

This conftest is the *only* place that:

* Resolves the ``YNAB_API_KEY`` (env var, otherwise an interactive prompt).
* Opens a live :class:`ymca.ynab_client.YnabClient` and wraps it with
  :class:`tests.integration.helpers.CountingYnabClient`.
* Resolves the id of the dedicated ``_Intergration Test_ USE ONLY`` plan and
  caches the list of open accounts within it.
* Empties that dedicated plan before the session starts and again at teardown
  (teardown is skipped when ``YNAB_INTEGRATION_LEAVE_DIRTY`` is set so the UI
  can be inspected).

All three behaviors are required by ``AGENTS.md``; centralizing them keeps the
individual test files focused on workflow assertions.
"""

from __future__ import annotations

import getpass
import os
import sys
import warnings
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
import ynab

from ymca.models import RemoteAccount, RemotePlan
from ymca.ynab_client import YnabClient

from .helpers import (
    INTEGRATION_PLAN_NAME,
    CountingYnabClient,
    clear_active_plan_transactions,
    ensure_integration_accounts,
)

__all__ = [
    "IntegrationEnvironment",
    "integration_env",
]


@dataclass(frozen=True)
class IntegrationEnvironment:
    """Resolved view of the YNAB test plan, reused by every test.

    Tests receive this via the ``integration_env`` fixture; it contains
    everything needed to avoid extra API calls during test bodies.
    """

    plan: RemotePlan
    accounts: tuple[RemoteAccount, ...]
    gateway: CountingYnabClient

    def accounts_by_name(self) -> dict[str, RemoteAccount]:
        return {account.name: account for account in self.accounts if not account.deleted}


def _resolve_api_key() -> str:
    """Return the YNAB API key from env, falling back to an interactive prompt.

    ``AGENTS.md`` requires integration tests to prompt if the env var is
    missing. We skip the prompt in non-interactive contexts (no TTY, captured
    stdin) so that CI runs without the env var fail soft instead of hanging or
    producing an opaque traceback.
    """
    env_key = os.environ.get("YNAB_API_KEY", "").strip()
    if env_key:
        return env_key

    if not sys.stdin.isatty():
        pytest.skip(
            "Integration tests require YNAB_API_KEY (env var or interactive "
            "prompt). Non-interactive context with no env var -- skipping."
        )

    try:
        prompted = getpass.getpass(
            "YNAB_API_KEY not set. Paste your YNAB Personal Access Token "
            "(integration tests only touch the '_Intergration Test_ USE ONLY' "
            "plan): "
        ).strip()
    except (EOFError, OSError):
        pytest.skip(
            "Integration tests require YNAB_API_KEY (env var or interactive "
            "prompt). Interactive prompt unavailable -- skipping."
        )
    if not prompted:
        pytest.skip("No YNAB API key provided; skipping integration tests.")
    return prompted


@pytest.fixture(scope="session")
def _ynab_api_key() -> str:
    return _resolve_api_key()


@pytest.fixture(scope="session")
def _ynab_client(_ynab_api_key: str) -> Iterator[YnabClient]:
    """Open one live YnabClient for the whole session."""
    with YnabClient(_ynab_api_key) as client:
        yield client


@pytest.fixture(scope="session")
def _api_client(_ynab_api_key: str) -> Iterator[ynab.ApiClient]:
    """Independent ApiClient for test-only SDK operations (create/delete/payees).

    We keep this separate from the one inside :class:`YnabClient` so that the
    wrapper in :class:`CountingYnabClient` holds a stable handle for its
    transactions/payees APIs without reaching into private attributes of the
    production adapter.
    """
    configuration = ynab.Configuration(access_token=_ynab_api_key)
    with ynab.ApiClient(configuration) as api_client:
        yield api_client


@pytest.fixture(scope="session")
def integration_env(
    _ynab_client: YnabClient, _api_client: ynab.ApiClient
) -> Iterator[IntegrationEnvironment]:
    """Return a fully prepared :class:`IntegrationEnvironment`.

    Steps (all cached once per session):

    1. Wrap the live client in a :class:`CountingYnabClient`.
    2. Resolve the test plan id by name.
    3. Fetch the plan's open accounts.
    4. Empty the dedicated test plan of any active transactions.
    5. Teardown: empty the dedicated test plan again after the session.
    """
    gateway = CountingYnabClient(_ynab_client, _api_client)

    plans = gateway.list_plans(include_accounts=False)
    matching = [plan for plan in plans if plan.name == INTEGRATION_PLAN_NAME]
    if not matching:
        pytest.fail(
            f"Integration test plan {INTEGRATION_PLAN_NAME!r} not found in "
            "YNAB. Create it manually per docs/testing.md before running "
            "integration tests."
        )
    if len(matching) > 1:
        pytest.fail(
            f"Multiple plans named {INTEGRATION_PLAN_NAME!r} exist; rename "
            "all but one before running integration tests."
        )
    plan = matching[0]
    gateway.set_allowed_write_plan_id(plan.id)

    account_snapshot = gateway.list_accounts(plan.id)
    accounts = ensure_integration_accounts(gateway, plan.id, account_snapshot.accounts)

    _clear_test_plan_transactions(gateway, plan)
    env = IntegrationEnvironment(
        plan=plan,
        accounts=accounts,
        gateway=gateway,
    )

    try:
        yield env
    finally:
        leave_dirty = os.environ.get("YNAB_INTEGRATION_LEAVE_DIRTY", "").strip().lower() in (
            "1",
            "true",
            "yes",
        )
        if not leave_dirty:
            _teardown_session_transactions(gateway, plan)


def _clear_test_plan_transactions(
    gateway: CountingYnabClient, plan: RemotePlan
) -> None:
    """Delete every active transaction still in the dedicated test plan."""
    clear_active_plan_transactions(gateway, plan.id)


def _teardown_session_transactions(
    gateway: CountingYnabClient, plan: RemotePlan
) -> None:
    """Delete every active transaction in the dedicated test plan.

    Teardown should be best-effort: if YNAB returns a transient 5xx during
    cleanup, surface a warning instead of turning a successful test session
    into an error at process shutdown.
    """
    try:
        _clear_test_plan_transactions(gateway, plan)
    except Exception as exc:
        warnings.warn(
            (
                "Integration test teardown could not fully clean the dedicated "
                f"test plan {plan.name!r}: {exc}"
            ),
            pytest.PytestWarning,
            stacklevel=2,
        )


