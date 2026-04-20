# YMCA Workflows

Every user-facing workflow is listed here with the tests that cover it. When a new workflow is added or discovered, it must be documented here before merging.

Legend:

- **Unit**: file under `tests/unit/` that covers the workflow in isolation with a `FakeGateway`.
- **Offline workflow**: file under `tests/workflows/` that exercises the workflow end-to-end against a stateful in-memory YNAB gateway.
- **Integration**: file under `tests/integration/` that exercises the workflow through the real YNAB adapter against the `_Intergration Test_ USE ONLY` plan.

## W1. `ymca config init`

Create a placeholder config file at the supplied (or default) path. Optional `--force` overwrites an existing file.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_config_init_writes_template`; [`tests/unit/test_config.py`](../tests/unit/test_config.py) — `test_write_config_template_overwrites_with_force`, `test_write_config_template_refuses_existing_file_without_force`, `test_write_config_template_creates_parent_directories`.
- Integration: not applicable (offline; writes to a local path only).

## W2. `ymca config check`

Validate a config file, confirm API-key access, authenticate against YNAB, resolve plan and account names.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_config_check_reports_success`.
- Integration: [`tests/integration/test_config_check_workflow.py`](../tests/integration/test_config_check_workflow.py) — resolves the test plan and accounts live.

## W3. `ymca discover`

List visible YNAB plans and their open accounts. Closed and deleted accounts are hidden.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_discover_hides_closed_accounts`, `test_discover_reports_when_no_plans_are_returned`, `test_discover_reports_when_plan_has_no_accounts`.
- Integration: [`tests/integration/test_discover_workflow.py`](../tests/integration/test_discover_workflow.py).

## W4. `ymca sync` (dry run)

Build the set of planned transaction updates without writing anything back. State file is not updated.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — multiple coverage tests including `test_execute_conversion_dry_run_returns_without_writing`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_sync_apply_then_quiet_delta_workflow`.
- Integration: [`tests/integration/test_sync_dry_run.py`](../tests/integration/test_sync_dry_run.py) — builds a `PreparedConversion` against the live seed; asserts no writes occurred.

## W5. `ymca sync --apply`

Persist converted amounts and memos to YNAB. Also saves refreshed `server_knowledge` to local state on success.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_execute_conversion_saves_follow_up_server_knowledge`, `test_execute_conversion_batches_writes_per_account`; [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_sync_apply_updates_state_file`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_sync_apply_then_quiet_delta_workflow`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — executes against the live test plan, verifies transactions are actually modified in YNAB.

## W6. `ymca sync --bootstrap-since YYYY-MM-DD`

Force the run to ignore saved `server_knowledge` and sync transactions from the given date.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_bootstrap_since_overrides_saved_server_knowledge`; [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_parse_date_argument_rejects_invalid_iso_date`, `test_parse_date_argument_accepts_iso_date`, `test_prompt_for_start_date_retries_until_valid_input`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_sync_bootstrap_and_account_filter_workflow`.
- Integration: [`tests/integration/test_sync_bootstrap_and_filter.py`](../tests/integration/test_sync_bootstrap_and_filter.py).

## W7. `ymca sync --account ALIAS`

Limit the sync to one or more configured account aliases. Unknown or disabled aliases raise `UserInputError`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_raises_for_unknown_account_alias`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_sync_bootstrap_and_account_filter_workflow`.
- Integration: [`tests/integration/test_sync_bootstrap_and_filter.py`](../tests/integration/test_sync_bootstrap_and_filter.py).

## W8. Legacy memo migration (deprecated helper)

Rewrite legacy `(FX rate: ...)` memos into the current `[FX] ... (rate: ... PAIR)` format. Split parents use a single `update_transaction` call; others are batched.

Script: [`deprecated/one_off_scripts/migrate_legacy_fx_memos.py`](../deprecated/one_off_scripts/migrate_legacy_fx_memos.py).

- Unit: [`tests/unit/test_migrate_legacy_fx_memos.py`](../tests/unit/test_migrate_legacy_fx_memos.py).
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_migrate_legacy_memo_workflow`.
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## W9. Double-conversion repair (deprecated helper)

Fix transactions that were converted twice: both a legacy and a current marker are present; the amount is the already-converted value rather than the original.

Script: [`deprecated/one_off_scripts/fix_double_converted_transactions.py`](../deprecated/one_off_scripts/fix_double_converted_transactions.py).

- Unit: [`tests/unit/test_fix_double_converted_transactions.py`](../tests/unit/test_fix_double_converted_transactions.py).
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_fix_double_converted_transaction_workflow`.
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## W10. Account delta inspection (deprecated helper)

Query YNAB for transactions changed since a given `last_knowledge_of_server`, account by account.

Script: [`deprecated/one_off_scripts/get_account_delta.py`](../deprecated/one_off_scripts/get_account_delta.py).

- Unit: [`tests/unit/test_get_account_delta.py`](../tests/unit/test_get_account_delta.py).
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_get_account_delta_workflow`.
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## W11. `ymca sync` with local currency tracking

Opt-in, per-account. When an account has `track_local_balance: true`, `ymca sync` additionally maintains a source-currency running balance on a dedicated YNAB sentinel transaction (payee name `[YMCA] Tracked Balance`, amount `0`, cleared status `reconciled`). Balance updates are delta-based: a new cleared/reconciled transaction adds to the balance, a subsequent delete of such a transaction subtracts; uncleared transitions are not tracked (see E24, E25). Tolerance check at the end of the run warns if the tracked balance drifts beyond `0.01` stronger-currency units vs YNAB's `cleared_balance`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) and [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — covers the transition matrix, sentinel upsert, tolerance math.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_local_currency_tracking_lifecycle_workflow`.
- Integration: [`tests/integration/test_local_currency_tracking.py`](../tests/integration/test_local_currency_tracking.py) — seeds cleared/uncleared/transfer rows on a tracked account and asserts the sentinel memo reflects the running balance.

## W12. `ymca sync --rebuild-balance`

Recovery mode for a tracked account whose sentinel has drifted (e.g. a cleared transaction was edited, un-cleared, or the sentinel was edited by hand). Ignores saved `server_knowledge` for the selected accounts, re-fetches every active transaction, parses both legacy `(FX rate: ...)` and current `[FX] ...` markers to derive the source amount per row, and recomputes the sentinel from scratch. Requires at least one account in scope with `track_local_balance: true`; mutually exclusive with `--bootstrap-since`. Prompts interactively for the direction of any zero-amount transfer encountered (see E22).

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py), [`tests/unit/test_balance.py`](../tests/unit/test_balance.py), [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — covers full-scan parsing, argparse wiring, mutex enforcement.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_local_currency_tracking_lifecycle_workflow`.
- Integration: [`tests/integration/test_local_currency_tracking.py`](../tests/integration/test_local_currency_tracking.py) — runs a rebuild after synthetic drift and verifies the sentinel is corrected.

## Path Resolution Workflows

These are cross-cutting behaviors exercised by several commands:

- `YMCA_CONFIG_PATH` / `YMCA_STATE_PATH` env overrides, XDG defaults — unit: [`tests/unit/test_paths.py`](../tests/unit/test_paths.py).
- API-key resolution order (`YNAB_API_KEY` → `secrets.api_key_file` → prompt) — unit: [`tests/unit/test_secrets.py`](../tests/unit/test_secrets.py).
