# YMCA Edge Cases

Every edge case is listed here with the tests that cover it. When a new edge case is discovered it must be added here **before** shipping test coverage, per `AGENTS.md`.

Legend:

- **Unit**: file/test under `tests/unit/`.
- **Integration**: file/test under `tests/integration/`.

The "Required by AGENTS.md" section covers the edge cases explicitly called out in `AGENTS.md §Testing Requirements`. The "Additional" section covers cases discovered in the codebase that extend beyond that list.

## Required by AGENTS.md

### E1. Zero-amount transactions (pre- and post-conversion)

A transaction with amount `0` must not be skipped; the FX marker is still appended (`0 <CCY>`), and the uploaded amount remains `0` regardless of the configured rate.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_keeps_zero_amount_transactions`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — seed includes a zero-amount transaction on a foreign account.

### E2. Transfer transactions

A transfer pair (one "out" side in the source account, one "in" side in the target account) must be converted **once**, not twice. The surviving side uses a `+/-` literal prefix in the memo. Processing must not depend on the order accounts are fetched.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_processes_transfer_once_with_plus_minus_prefix`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — seed includes an HKD↔GBP transfer pair; post-apply state asserts only one side is rewritten.

### E3. Transactions with split categories

Transactions whose `subtransaction_count > 0` are skipped by the main `sync` path. (The legacy migration helper handles split parents specifically; see E9.)

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_deleted_and_split_transactions`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — seed includes a split transaction; integration asserts the main converter produced `skipped(reason="split")` and the YNAB record is untouched.

### E4. Transfer transactions with split categories

A split on the outflow leg of a transfer is still skipped by the main converter (split rule wins over transfer handling), and the corresponding inflow leg in the paired account is also left untouched. This combines E2 and E3.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_split_transfer_is_skipped_not_converted`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — seed includes a transfer pair where one side is split into subtransactions; post-apply asserts neither leg was modified.

## Additional edge cases discovered in the codebase

### E5. Transaction already carrying the current `[FX]` marker

Skipped with reason `already-converted`; the amount is not double-converted.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_uses_milliunit_precision_and_skips_marked_transactions`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py).

### E6. Transaction carrying the legacy `(FX rate: ...)` marker

Skipped by the main CLI with reason `legacy-marker`; handled only by the migration helper.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_legacy_marked_transactions`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — verifies the main CLI skips legacy-marked rows in live YNAB data.

### E7. Deleted transactions

Transactions with `deleted: true` are skipped. They must not contribute to `writes_performed`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_deleted_and_split_transactions`.
- Integration: covered implicitly by the leftover-sweep teardown; deleted transactions in the test plan never appear in subsequent runs' work set.

### E8. Milliunit-precision conversion (HKD divide)

YNAB amounts are milliunits; rounding happens at milliunit precision, not cent precision. `12340` at `7.8 HKD/USD` uploads `1582`, not `1580`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — asserts `converted_amount_milliunits == 1582` in the HKD tests.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — HKD seed uses an amount sensitive to milliunit rounding, asserts the post-apply amount matches.

### E9. Multiply FX path (`divide_to_base: false`, GBP)

For GBP with `divide_to_base: false`, `base = source * rate`. Conversion math and memo pair label (`USD/GBP`) must reflect this direction.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_multiply_path_when_divide_false`.
- Integration: [`tests/integration/test_sync_apply.py`](../tests/integration/test_sync_apply.py) — GBP seed covers this direction.

### E10. Long-rate memo rounding

The `rate_text` embedded in new `[FX]` memos is rounded to three decimal places (`ROUND_HALF_UP`) and normalized (trailing zeros trimmed), even when the configured `rate` has more precision. Conversion math still uses full precision.

- Unit: [`tests/unit/test_config.py`](../tests/unit/test_config.py) — `test_parse_rate_rounds_to_three_decimal_places`.
- Integration: [`tests/integration/test_sync_dry_run.py`](../tests/integration/test_sync_dry_run.py) — uses a four-decimal rate and asserts the memo shows three places.

### E11. Double-converted transactions (legacy repair pattern)

A transaction whose memo contains **both** a legacy marker and a current marker, where the amount equals the once-converted amount rather than the original. Requires `fix_double_converted_transactions.py` to repair (amount reset to the legacy substring, legacy segment stripped from memo).

- Unit: [`tests/unit/test_fix_double_converted_transactions.py`](../tests/unit/test_fix_double_converted_transactions.py).
- Integration: not applicable (`deprecated/one_off_scripts/` stays unit-tested only).

### E12. Unresolved legacy marker (no pair label configured)

If the legacy-memo helper encounters a legacy marker whose currency has no configured `fx_rates` entry, the transaction is skipped with reason `unconfigured-legacy-marker`, not rewritten.

- Unit: [`tests/unit/test_migrate_legacy_fx_memos.py`](../tests/unit/test_migrate_legacy_fx_memos.py) — `test_migrate_legacy_fx_memos_script_skips_unconfigured_currency`.
- Integration: not required (pure memo-parsing behavior).

### E13. Empty YNAB plan list

`ymca discover` prints `No YNAB plans found.` and exits `0` when the authenticated user has no plans.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_discover_reports_when_no_plans_are_returned`.
- Integration: not exercised (the live user always has ≥1 plan).

### E14. Duplicate / missing plan or account name

`resolve_bindings` raises `ApiError` when the configured plan name matches zero or multiple YNAB plans, or when a configured account name matches zero or multiple accounts in the resolved plan.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_resolve_bindings_raises_when_plan_not_found`, `test_resolve_bindings_raises_when_multiple_plans_match`, `test_resolve_bindings_raises_when_account_missing`, `test_resolve_bindings_raises_when_account_matches_multiple`, `test_resolve_bindings_skips_deleted_remote_accounts`.
- Integration: not exercised (would require non-deterministic changes to the user's YNAB).

### E15. Bootstrap with no saved server knowledge

When there is no `server_knowledge` for the plan and no `--bootstrap-since`, the CLI prompts the user for a start date.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_prompt_for_start_date_retries_until_valid_input`; [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_prompts_when_no_bootstrap_or_state`.
- Integration: not exercised (the test plan always has server knowledge after the first seed).

### E16. Invalid `--bootstrap-since` argument

An unparseable date on the CLI raises `argparse.ArgumentTypeError` and exits non-zero.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_parse_date_argument_rejects_invalid_iso_date`.

### E17. `YmcaError` → exit 1; `KeyboardInterrupt` → exit 130

`main()` wraps dispatch in a handler that returns these exit codes for the two expected exception classes.

- Unit: [`tests/unit/test_cli.py`](../tests/unit/test_cli.py) — `test_main_translates_ymca_error_to_exit_code_one`, `test_main_translates_keyboard_interrupt_to_one_thirty`.

### E18. Soft-delete preservation in integration test plan

YNAB `DELETE /transactions/{id}` is soft; cleaned transactions remain in the plan with `deleted: true`. The harness relies on this being a no-op for active-state assertions and wipes every active transaction in the dedicated test plan before and after the session.

- Integration: [`tests/integration/conftest.py`](../tests/integration/conftest.py) — plan wipe + session teardown.

### E19. Rate-limit (429) handling

A `429 Too Many Requests` from YNAB triggers one retry after honoring `Retry-After`; a subsequent 429 fails the session with an actionable message rather than cascading into every remaining test.

- Integration: [`tests/integration/helpers.py`](../tests/integration/helpers.py) — `CountingYnabClient._invoke_with_backoff`.

### E20. Per-session budget cap

If the integration harness makes more than the configured number of SDK calls in a session (default 150), it aborts with `BudgetExceededError`. This protects the YNAB API key from being locked out during a buggy run.

- Integration: [`tests/integration/helpers.py`](../tests/integration/helpers.py) — `CountingYnabClient._check_budget`.
