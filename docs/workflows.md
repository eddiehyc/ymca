# YMCA Workflows

Every user-facing workflow is listed here with the tests that cover it. When a new workflow is added or discovered, it must be documented here before merging.

Legend:

- **Unit**: file under `tests/unit/` that covers the workflow in isolation with a `FakeGateway`.
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

## W4. `ymca convert` (dry run)

Build the set of planned transaction updates without writing anything back. State file is not updated.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — multiple coverage tests including `test_execute_conversion_dry_run_returns_without_writing`.
- Integration: [`tests/integration/test_convert_dry_run.py`](../tests/integration/test_convert_dry_run.py) — builds a `PreparedConversion` against the live seed; asserts no writes occurred.

## W5. `ymca convert --apply`

Persist converted amounts and memos to YNAB. Also saves refreshed `server_knowledge` to local state on success.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_execute_conversion_saves_follow_up_server_knowledge`, `test_execute_conversion_batches_writes_per_account`; [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_convert_apply_updates_state_file`.
- Integration: [`tests/integration/test_convert_apply.py`](../tests/integration/test_convert_apply.py) — executes against the live test plan, verifies transactions are actually modified in YNAB.

## W6. `ymca convert --bootstrap-since YYYY-MM-DD`

Force the run to ignore saved `server_knowledge` and sync transactions from the given date.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_bootstrap_since_overrides_saved_server_knowledge`; [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_parse_date_argument_rejects_invalid_iso_date`, `test_parse_date_argument_accepts_iso_date`, `test_prompt_for_start_date_retries_until_valid_input`.
- Integration: [`tests/integration/test_convert_bootstrap_and_filter.py`](../tests/integration/test_convert_bootstrap_and_filter.py).

## W7. `ymca convert --account ALIAS`

Limit conversion to one or more configured account aliases. Unknown or disabled aliases raise `UserInputError`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_raises_for_unknown_account_alias`.
- Integration: [`tests/integration/test_convert_bootstrap_and_filter.py`](../tests/integration/test_convert_bootstrap_and_filter.py).

## W8. Legacy memo migration (deprecated helper)

Rewrite legacy `(FX rate: ...)` memos into the current `[FX] ... (rate: ... PAIR)` format. Split parents use a single `update_transaction` call; others are batched.

Script: [`deprecated/one_off_scripts/migrate_legacy_fx_memos.py`](../deprecated/one_off_scripts/migrate_legacy_fx_memos.py).

- Unit: [`tests/unit/test_migrate_legacy_fx_memos.py`](../tests/unit/test_migrate_legacy_fx_memos.py).
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## W9. Double-conversion repair (deprecated helper)

Fix transactions that were converted twice: both a legacy and a current marker are present; the amount is the already-converted value rather than the original.

Script: [`deprecated/one_off_scripts/fix_double_converted_transactions.py`](../deprecated/one_off_scripts/fix_double_converted_transactions.py).

- Unit: [`tests/unit/test_fix_double_converted_transactions.py`](../tests/unit/test_fix_double_converted_transactions.py).
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## W10. Account delta inspection (deprecated helper)

Query YNAB for transactions changed since a given `last_knowledge_of_server`, account by account.

Script: [`deprecated/one_off_scripts/get_account_delta.py`](../deprecated/one_off_scripts/get_account_delta.py).

- Unit: [`tests/unit/test_get_account_delta.py`](../tests/unit/test_get_account_delta.py).
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

## Path Resolution Workflows

These are cross-cutting behaviors exercised by several commands:

- `YMCA_CONFIG_PATH` / `YMCA_STATE_PATH` env overrides, XDG defaults — unit: [`tests/unit/test_paths.py`](../tests/unit/test_paths.py).
- API-key resolution order (`YNAB_API_KEY` → `secrets.api_key_file` → prompt) — unit: [`tests/unit/test_secrets.py`](../tests/unit/test_secrets.py).
