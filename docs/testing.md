# YMCA Testing Policy

This document describes how YMCA tests are organized, how they are run, and the constraints the integration suite operates under.

## 1. Scope

Tests live under the `tests/` directory and are split into three complementary suites:

- **Unit tests** (`tests/unit/`): fast, offline, no network. They use the `FakeGateway` in `tests/fakes.py` to stand in for YNAB.
- **Offline workflow tests** (`tests/workflows/`): end-to-end, offline, no network. They use a stateful in-memory YNAB gateway to exercise real CLI and deprecated-script flows across multiple steps and persisted local state.
- **Integration tests** (`tests/integration/`): exercise the real YNAB HTTP API against a dedicated test plan. Marked with `@pytest.mark.integration`.

Deprecated one-off scripts under `deprecated/one_off_scripts/` are covered by
unit tests only. The live integration budget is reserved for the supported
CLI and adapter workflows under `src/ymca`.

The canonical local check flow is:

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests
uv run pytest
```

Per `AGENTS.md`, `uv run pytest` runs both suites by default.

## 2. Unit Tests

### 2.1 Coverage Requirement

The unit suite must achieve at least **90% line coverage** on `src/ymca/`, enforced through `pytest-cov`:

```toml
[tool.pytest.ini_options]
addopts = [
    "--cov=src/ymca",
    "--cov-report=term-missing",
    "--cov-fail-under=90",
]
```

Running `uv run pytest` fails if coverage drops below 90%. Coverage is measured against both unit and integration runs, so a full run with real credentials includes adapter coverage too; a unit-only run (`uv run pytest -m "not integration"`) must still reach 90% on its own.

### 2.2 Mocking Policy

- External interactions (YNAB SDK, filesystem prompts, `input`, `getpass`) are mocked or patched.
- The YNAB SDK adapter (`src/ymca/ynab_client.py`) is unit-tested by monkeypatching `ynab.PlansApi`, `ynab.AccountsApi`, `ynab.TransactionsApi`, and `ynab.ApiClient` in-place.
- Business logic is tested through the `YnabGateway` protocol using `FakeGateway`.

## 3. Integration Tests

### 3.1 Dedicated Plan

Per `AGENTS.md`:

- All writes must happen inside the YNAB plan named exactly `_Intergration Test_ USE ONLY`.
- The plan ID is resolved at session start via `PlansApi.get_plans` — never hardcoded.
- `YNAB_API_KEY` must be provided as an env var. If missing, the harness prompts interactively (`getpass.getpass`).

**Manual prerequisites** (one-time setup in YNAB):

1. Create a budget named exactly `_Intergration Test_ USE ONLY`.
2. Set its base currency to `USD`.
3. Leave the plan otherwise empty.

The integration harness provisions the expected on-budget foreign-currency
accounts inside that dedicated plan if they are missing:

- `HKD Integration`
- `HKD Integration 2`
- `GBP Integration`

This keeps the live suite independent from any checked-in or hand-authored
config file. Tests still build config objects in memory, but the live YNAB
accounts they point at are created by the harness itself.

If the plan already contains conflicting closed or duplicate accounts using
those reserved names, the suite fails with a setup error rather than creating
ambiguous duplicates.

### 3.2 Rate-Limit Strategy

The YNAB API caps at **200 requests per hour per API key**. The integration harness is designed to stay well under this cap so developers can run the suite multiple times per hour:

| Phase | Approximate request count |
|-------|---------------------------|
| Session setup (`list_plans`, `list_accounts`, one `list_transactions_by_account` per account, leftover sweep) | 6 – 10 |
| Shared seed (`create_transactions` batch) | 1 |
| Per workflow test (list delta + optional update batch) | ~3 |
| Session teardown (one `delete_transaction` per created transaction) | ~20 |
| **Total per session** | **~50** |

Guardrails:

- `CountingYnabClient` wraps the real adapter, counts every SDK call, and raises `BudgetExceededError` if the per-session count exceeds a configurable budget (default `150`).
- 429 responses trigger a single retry after honoring the `Retry-After` header; a second 429 fails fast with an actionable message.
- Every write (`update_transaction`, `update_transactions`, `delete_transaction`) is gated by a runtime check that the target `plan_id` equals the resolved test plan ID.
- Cleanup uses multiple sweep passes and retries transient `429`/`5xx` failures so one flaky delete does not leave the plan dirty for the next run.

If you run the suite repeatedly and start hitting the rate limit, you can isolate to unit tests only:

```bash
uv run pytest -m "not integration"
```

### 3.3 Session Fixtures and Cleanup

Every integration run:

1. Resolves the test plan and its accounts once (cached for the session).
2. **Plan wipe**: deletes every active transaction already present in the dedicated test plan, retrying transient failures and re-listing until the plan is verified empty.
3. Runs workflow tests. The consolidated live scenario is
   [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py)
   (`test_integration_session_all_workflows`): one ordered run that seeds a shared
   dataset, applies incrementally, and stays within the API budget.
4. **Session teardown**: deletes every active transaction still present in the
   dedicated test plan using the same retrying verification sweep — **unless**
   `YNAB_INTEGRATION_LEAVE_DIRTY` is set to `1`/`true`/`yes` (inspect the plan in
   the YNAB UI; the next session still wipes at startup).

The harness treats all seeded rows as normal manually entered transactions. It
does **not** rely on `import_id` tagging. Tests identify their own seeded rows
using unique payee names and other transaction content that they control.

### 3.4 YNAB Soft-Delete Behavior

`TransactionsApi.delete_transaction` performs a **soft** delete: the transaction is marked `deleted: true` but remains in the plan. "Clean state" in this document therefore means "no active (`deleted: false`) transactions that this tooling created". Over many runs the plan accumulates deleted records; this is expected and harmless.

### 3.5 Shared Seed Dataset

The consolidated session test seeds payee-tagged rows that cover dry-run skips
(split, legacy, already-converted), tracked-balance transitions, GBP/HKD
conversion, optional HKD transfers, delete/reversal, and rebuild — see
[workflows.md](workflows.md) and [edge-cases.md](edge-cases.md). The session-level
plan wipe clears leftovers before the run; per `AGENTS.md`, only the dedicated
test plan is mutated.

## 4. Adding New Tests

When you discover a new workflow or edge case:

1. **Document first.** Add an entry to [workflows.md](workflows.md) or [edge-cases.md](edge-cases.md) describing what the case is and what the expected behavior should be. This is required by `AGENTS.md`.
2. **Unit test.** Add a unit test that covers the business logic in isolation using `FakeGateway`.
3. **Integration test.** Add an integration test (marked `@pytest.mark.integration`) that exercises the case through the real adapter against the test plan when the behavior belongs to the supported CLI or adapter surface. Deprecated one-off scripts stay unit-tested only unless explicitly promoted back into supported functionality.
4. **Budget check.** After adding an integration test, run the full suite and confirm the per-session request count stays under the 150-call budget. If you need more, raise it explicitly in `tests/integration/helpers.py` and note the new budget here.

## 5. Where Things Live

| Path | Purpose |
|------|---------|
| `tests/unit/` | Offline unit tests. |
| `tests/unit/test_ynab_client.py` | Adapter coverage via `ynab.*Api` monkeypatching. |
| `tests/fakes.py` | `FakeGateway`, shared by unit tests only. |
| `tests/workflows/` | Offline end-to-end workflow tests with a stateful in-memory gateway. |
| `tests/workflows/helpers.py` | `InMemoryGateway`, shared by offline workflow tests. |
| `tests/integration/conftest.py` | Session-scoped fixtures: API key, plan lookup, accounts, seed, teardown, rate-limit guard. |
| `tests/integration/helpers.py` | `CountingYnabClient`, account provisioning, SDK delete helper, seed builders. |
| `tests/integration/test_*.py` | Live coverage for supported workflows, all marked `integration` (main scenario: `test_z_integration_session_workflow.py`). |
| `docs/testing.md` | This file. |
| `docs/workflows.md` | Every workflow mapped to its covering tests. |
| `docs/edge-cases.md` | Every edge case mapped to its covering tests. |
