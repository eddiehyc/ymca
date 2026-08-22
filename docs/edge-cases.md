# YMCA Edge Cases

Every edge case is listed here with the tests that cover it. When a new edge case is discovered it must be added here **before** shipping test coverage, per `AGENTS.md`.

Legend:

- **Unit**: file/test under `tests/unit/`.
- **Offline workflow**: file/test under `tests/workflows/`.
- **Integration**: file/test under `tests/integration/`.

The "Required by AGENTS.md" section covers the edge cases explicitly called out in `AGENTS.md §Testing Requirements`. The "Additional" section covers cases discovered in the codebase that extend beyond that list.

## Required by AGENTS.md

### E1. Zero-amount transactions (pre- and post-conversion)

A transaction with amount `0` must not be skipped; the FX marker is still appended (`0 <CCY>`), and the uploaded amount remains `0` regardless of the configured rate.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_keeps_zero_amount_transactions`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — seed includes a zero-amount transaction on a foreign account.

### E2. Transfer transactions

A transfer pair (one "out" side in the source account, one "in" side in the target account) must be converted **once**, not twice. The surviving side uses a `+/-` literal prefix in the memo. Processing must not depend on the order accounts are fetched.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_processes_transfer_once_with_plus_minus_prefix`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — when `HKD Integration 2` exists, seeds an HKD↔HKD transfer; `PreparedConversion` asserts exactly one transfer update and at least one `paired-transfer` skip.

### E3. Transactions with split categories

Transactions whose `subtransaction_count > 0` are skipped by the main `sync` path. (The legacy migration helper handles split parents specifically; see E9.)

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_deleted_and_split_transactions`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — seed includes a split transaction; integration asserts the main converter produced `skipped(reason="split")` and the YNAB record is untouched.

### E4. Transfer transactions with split categories

A split on the outflow leg of a transfer is still skipped by the main conversion pass (split rule wins over transfer handling), and the corresponding inflow leg in the paired account is also left untouched there. Already-marked split transfer parents can still be read during tracked-balance delta/rebuild runs, but if YMCA would need to rewrite their memo, the run now fails with a manual-action message because YNAB's API does not support updating transfer transactions with split categories reliably. The user must paste the suggested memo into the YNAB web UI and rerun the sync.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_split_transfer_is_skipped_not_converted`, `test_build_prepared_conversion_deduplicates_transfer_pairs`, `test_build_prepared_conversion_rebuild_balance_errors_for_split_transfer_memo_flip`, `test_build_prepared_conversion_allows_split_transfer_when_no_memo_flip_is_needed`; [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — `test_rebuild_transfer_memo_flip_preserves_payee_id`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_rebuild_tracking_errors_for_split_transfer_parent_workflow`.
- Integration: not exercised live (split transfer parents are unreliable via the YNAB API); offline workflow above covers the expectation.

## Additional edge cases discovered in the codebase

### E5. Transaction already carrying the current `[FX]` marker

Skipped with reason `already-converted`; the amount is not double-converted.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_uses_milliunit_precision_and_skips_marked_transactions`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — seed includes an already-marked HKD row; `PreparedConversion` skips it with `already-converted`.

### E6. Transaction carrying the legacy `(FX rate: ...)` marker

Skipped by the main CLI with reason `legacy-marker`; handled only by the migration helper.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_legacy_marked_transactions`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — verifies the main CLI skips legacy-marked rows in live YNAB data.

### E7. Deleted transactions

Transactions with `deleted: true` are skipped. They must not contribute to `writes_performed`.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_deleted_and_split_transactions`.
- Integration: covered implicitly by the leftover-sweep teardown; deleted transactions in the test plan never appear in subsequent runs' work set.

### E8. Cent-precision conversion (HKD divide)

YNAB amounts are milliunits, but FX-converted uploads are rounded to the nearest cent (10 milliunits) so the displayed account balance equals the sum of stored transaction amounts. `12340` at `7.8 HKD/USD` uploads `1580`, not `1582`. See §7.3 of [`spec.md`](spec.md).

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_convert_amount_milliunits_stores_cent_aligned_amounts` parametrically asserts cent alignment; the HKD round-trip test asserts `converted_amount_milliunits == 1580`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — HKD seed uses an amount sensitive to rounding direction (`-12340 HKD / 7.8`) and asserts the post-apply amount is `-1580` and cent-aligned (`% 10 == 0`).

### E9. Multiply FX path (`divide_to_base: false`, GBP)

For GBP with `divide_to_base: false`, `base = source * rate`. Conversion math and memo pair label (`USD/GBP`) must reflect this direction.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_multiply_path_when_divide_false`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — GBP seed covers this direction.

### E10. Long-rate memo rounding

The `rate_text` embedded in new `[FX]` memos is rounded to three decimal places (`ROUND_HALF_UP`) and normalized (trailing zeros trimmed), even when the configured `rate` has more precision. Conversion math still uses full precision.

- Unit: [`tests/unit/test_config.py`](../tests/unit/test_config.py) — `test_parse_rate_rounds_to_three_decimal_places`.
- Integration: not duplicated live (integration `rate_text` values are short); the unit test above is canonical for multi-decimal `rate` → three-decimal memo behavior.

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

YNAB `DELETE /transactions/{id}` is soft; cleaned transactions remain in the plan with `deleted: true`. The harness relies on this being a no-op for active-state assertions and wipes every active transaction in the dedicated test plan before the session and after the session unless `YNAB_INTEGRATION_LEAVE_DIRTY` is set.

- Integration: [`tests/integration/conftest.py`](../tests/integration/conftest.py) — plan wipe + session teardown.

### E19. Rate-limit (429) handling

A `429 Too Many Requests` from YNAB triggers one retry after honoring `Retry-After`; a subsequent 429 fails the session with an actionable message rather than cascading into every remaining test.

- Integration: [`tests/integration/helpers.py`](../tests/integration/helpers.py) — `CountingYnabClient._invoke_with_backoff`.

### E20. Per-session budget cap

If the integration harness makes more than the configured number of SDK calls in a session (default 150), it aborts with `BudgetExceededError`. This protects the YNAB API key from being locked out during a buggy run.

- Integration: [`tests/integration/helpers.py`](../tests/integration/helpers.py) — `CountingYnabClient._check_budget`.

### E21. Zero-amount non-transfer contributing to tracked balance

A cleared or reconciled transaction whose YNAB base-currency amount is `0` may still carry a signed memo amount (e.g. FX conversion rounded `0.499 HKD` to `0`). Sign inference falls back to the memo sign when the YNAB amount is `0` and the transaction is not a transfer.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py).
- Integration: not covered live (needs a contrived zero-amount cleared row); unit tests above cover the branch.

### E22. Zero-amount transfer — interactive direction prompt

A transfer transaction whose YNAB amount is `0` carries a `+/-` literal prefix in the memo; direction cannot be inferred from either source. During `ymca sync --rebuild-balance`, the CLI prompts interactively per offending row (`(i)n/(o)ut/(s)kip`). Non-TTY with `--apply` fails fast. Dry-run without a TTY surfaces the ambiguous rows in the summary and skips them.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py), [`tests/unit/test_cli.py`](../tests/unit/test_cli.py).
- Integration: not covered by the live suite (interactive prompt); asserted via unit tests.

### E23. Balance transitions under the dual-marker rule

The delta classifier compares the counted bit in the FX marker (`[FX+]` vs. `[FX]`) against the current cleared/deleted state, and acts on the 2×2:

| `was_counted` | `should_be_counted` | Balance | Memo flip |
|---------------|---------------------|---------|-----------|
| False | False | — | migrate legacy → `[FX]` on first touch |
| False | True  | add | flip to `[FX+]` |
| True  | False | subtract | flip to `[FX]` |
| True  | True  | — | — |

All user-visible status transitions reduce to one cell:

- `uncleared → cleared/reconciled` on a `[FX]` row → add, flip to `[FX+]`.
- `cleared → reconciled` on `[FX+]` → no-op (the fix for the former double-count bug).
- `cleared → uncleared` on `[FX+]` → subtract, flip to `[FX]`.
- `cleared → deleted` on `[FX+]` → subtract, flip to `[FX]`.
- `uncleared → deleted` on `[FX]` → no-op.
- `cleared → uncleared → cleared` (flip-flop): subtract then add ⇒ net zero.

Coverage:

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — one test per cell of the matrix plus dedicated regressions (`test_delta_cleared_to_reconciled_is_noop_on_counted_row`, `test_delta_counted_to_uncleared_subtracts_and_flips_back`, `test_delta_migrates_legacy_marker_to_counted_and_contributes`, `test_build_tracking_update_subtracts_counted_then_deleted`).
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — the full-lifecycle test drives cleared → deleted and asserts the sentinel memo reflects the subtracted balance.

### E24. Legacy `(FX rate: ...)` markers on tracked accounts

Legacy markers are treated as `was_counted=False`. On their first appearance in a tracked account's delta:

- Currently cleared + not deleted → add source amount, migrate memo to `[FX+]`.
- Currently uncleared or deleted → no balance change, migrate memo to `[FX]` so the row doesn't need re-examination on subsequent runs.

Coverage:

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — `test_delta_migrates_legacy_marker_to_counted_and_contributes`, `test_delta_migrates_legacy_marker_to_uncounted_on_uncleared_row`.

### E25. Editing a cleared/reconciled transaction that YMCA has already FX-converted — unsupported

The marker records the source-currency amount at FX-conversion time. Any subsequent edit to the YNAB amount (alone, with the memo wiped, or alongside a hand-edited memo) cannot be reconciled automatically — see `docs/spec.md` §12.8 for the breakdown of the three sub-scenarios.

**Recommended workflow**: delete the row in YNAB and enter a replacement. The delete path (`[FX+]` + cleared + deleted) subtracts the old contribution and flips the memo back to `[FX]`; the new entry adds with a fresh `[FX+]` marker. Net effect: `−old + new` with no drift and no manual state juggling.

**Recovery from drift caused by prior hand-edits**: `ymca sync --rebuild-balance --apply`. Rebuild re-derives the balance from all active cleared FX-marked rows and re-normalizes every marker bracket to match its current cleared/deleted state.

- Unit: (intentionally not exercised — the bad states are user-created; the model simply has no safe action to take).
- Integration: (not a supported workflow).

### E26. First-time tracking enablement — bootstrap sentinel from history

The first `ymca sync` after `track_local_balance: true` is added to an account creates the sentinel transaction by scanning the current delta and computing the initial balance. If the delta window does not cover historical transactions, users are expected to follow up with `ymca sync --rebuild-balance` to catch up.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py).
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — asserts the sentinel appears on first apply run.

### E27. Tolerance check ≤ 0.02 stronger currency

At the end of every sync run (delta or rebuild), YMCA compares the tracked source-currency balance to YNAB's reported `cleared_balance` in base currency. Drift is reported in the "stronger currency" (base when `divide_to_base: true`; source otherwise). Drift beyond `0.02` of that unit prints a warning and suggests `ymca sync --rebuild-balance`; the run itself still exits `0`.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — exact-boundary, under, and over cases for both FX directions.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — drift-free path under live data.

### E28. Sentinel transaction detection and exclusion

The sentinel is identified by exact payee-name match against `[YMCA] Tracked Balance`. The sentinel row:

- is never FX-converted,
- is never counted towards the tracked balance,
- is upserted (created on first enablement, updated thereafter) at the end of each sync run for every tracked account in scope,
- carries a green `flag_color`, re-applied on every write so a hand-cleared flag in the YNAB UI gets restored automatically on the next run.

The sentinel's YNAB transaction id is persisted in `state.yaml` under `plans.<alias>.sentinel_ids.<account_alias>` so that quiet delta runs (where `server_knowledge` already advanced past the last sentinel write, and the delta therefore returns nothing) can still find and update the existing sentinel via `get_transaction_detail`. Without this direct lookup, a scan-only strategy would miss the sentinel on the next `ymca sync` after a successful apply and create a duplicate.

### E29. Sentinel deleted or re-tagged by user

If the saved `sentinel_id` points at a transaction that YNAB reports as `deleted=true`, or whose payee has been renamed off `[YMCA] Tracked Balance`, the sync treats the sentinel as missing and queues a fresh `create_transaction`. The new id is then persisted in `state.yaml`, overwriting the stale one.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — `test_sentinel_create_and_update_carry_the_green_flag` plus the adapter's `test_ynab_client_create_transaction_forwards_flag_color_when_set` / `test_ynab_client_update_transaction_forwards_flag_color_when_set`; [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_fetches_saved_sentinel_when_delta_is_empty`, `test_build_prepared_conversion_recreates_sentinel_when_user_deletes_it`, `test_execute_conversion_persists_new_sentinel_ids_in_state`.
- Integration: not covered live; unit tests above cover sentinel re-creation.

### E30. Partial-clear transfer pairs on tracked accounts

Transfer pairs share one YNAB memo, but local-currency tracking needs to know whether neither side, one side, or both sides have already been counted. YMCA therefore uses four transfer-aware marker states: `[FX]` for neither side counted, `[FX+]` for both, `[FX→]` when only the outflow side is counted, and `[FX←]` when only the inflow side is counted. When the second side clears, the marker promotes to `[FX+]`; when one side is un-cleared later, it demotes back to the directional form without losing the paired side's counted state.

- Unit: [`tests/unit/test_memo.py`](../tests/unit/test_memo.py), [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py), [`tests/unit/test_balance.py`](../tests/unit/test_balance.py).
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_transfer_tracking_partial_clear_workflow`.
- Integration: [`tests/integration/test_z_integration_session_workflow.py`](../tests/integration/test_z_integration_session_workflow.py) — optional HKD↔HKD transfer with one leg cleared first, then both.

### E31. Legacy memo migration for split transfer parents

The deprecated `migrate_legacy_fx_memos.py` helper cannot safely migrate split transfer parents through the YNAB API. Even a memo-only update on that row shape is not reliably supported, so those transactions remain manual-only in the YNAB web UI.

- Historical local-only coverage still exists in [`tests/unit/test_migrate_legacy_fx_memos.py`](../tests/unit/test_migrate_legacy_fx_memos.py) and [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py), but those fakes do not prove live YNAB API support for this row shape.

### E32. YNAB windows transaction fetches to ~12 months when `since_date` is omitted

YNAB's transactions endpoints (`GET .../accounts/{id}/transactions` and the budget-wide variant) silently limit results to roughly the trailing 12 months when no `since_date` is passed. Crucially, supplying `last_knowledge_of_server` does **not** bypass the window: a knowledge-only delta omits changed rows whose *date* is older than the window, and a `since_date`-less "full scan" omits old rows entirely.

Observed consequences before the fix:

- `--rebuild-balance` full scans silently dropped every row older than 12 months, undercounting the tracked balance by exactly the sum of the aged-out cleared rows (discovered live: a rebuild missed an account's first two months — including its Starting Balance — once the account crossed the 12-month mark, while the drift check correctly compared against YNAB's full-history `cleared_balance` header).
- Delta runs could never see edits (e.g. un-clearing) made to rows older than the window, so the memo ledger would drift silently.

Fix: every transactions fetch sends an explicit `since_date`. When no user-facing date applies, `FULL_HISTORY_SINCE_DATE` (`2000-01-01`, defined in `ymca.conversion`) is sent as a floor; a fixed floor is used instead of the plan's `first_month` because YNAB allows transactions backdated before plan creation. A user-provided `--bootstrap-since` date still takes precedence. The integration cleanup sweep passes the same floor so backdated seed rows cannot survive the plan wipe.

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_delta_fetch_carries_since_date_floor`, `test_build_prepared_conversion_rebuild_balance_full_scans_only_tracked_accounts` (floor on full scans), `test_execute_conversion_saves_follow_up_server_knowledge` (floor on the post-write knowledge refresh).
- Integration: [`tests/integration/test_sync_full_history_window.py`](../tests/integration/test_sync_full_history_window.py) — seeds a cleared row backdated ~14 months and asserts a rebuild full scan against the live API still fetches, converts, and counts it.

### E33. Transaction detail 404 after list/delta surfaces the id

YNAB account transaction lists (including knowledge deltas) can return a transaction id that a subsequent `GET .../transactions/{id}` rejects with `404.2 resource_not_found`. Soft-deleted rows normally arrive with `deleted: true` and are skipped earlier; this path covers hard-missing ids (or a race where the row vanishes between list and detail).

Behavior: the FX candidate loop catches that 404, skips the row with reason `missing`, and continues preparing other candidates. Non-404 detail failures still abort. Adapter errors for `get_transaction_detail` include `transaction_id=` so remaining failures are diagnosable. Saved-sentinel and paired-transfer-leg 404s remain handled separately (recreate sentinel / treat pair as not counted).

- Unit: [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_skips_missing_detail_404`, `test_build_prepared_conversion_reraises_non_404_detail_errors`; [`tests/unit/test_ynab_client.py`](../tests/unit/test_ynab_client.py) — `test_ynab_client_get_transaction_detail_wraps_exception`.
- Integration: not forced live (requires a list/detail inconsistency the API does not expose for seeding).

### E34. Row entered as cleared drifts against the pre-conversion `cleared_balance`

`cleared_balance` is read from the account snapshot at the start of the run, before any FX write lands. A row the user entered as *cleared* is therefore still counted there at its **source-currency** amount, while the tracked balance already counts it as source currency destined for conversion. Comparing the two directly reports the entire FX spread of that row as drift.

Observed: an HKD account holding 780.00 HKD (100.00 USD in YNAB) plus a newly entered cleared 78 HKD row was reported as `-68.00 USD (DRIFT)` and told the user to run `ymca sync --rebuild-balance`. Nothing was wrong: the run's own write brings the account to 110.00 USD, matching the 858.00 HKD tracked balance. Re-running the sync after the apply reported no drift, so the warning was a one-run artifact of comparing a post-write figure against a pre-write one.

Fix: the tolerance check rebases onto the balance YNAB will report *after* this run's writes (`docs/spec.md` §12.6). Only cleared/reconciled rows join the projection — rewriting an uncleared row moves `uncleared_balance`, not `cleared_balance` — and a transfer write is mirrored onto the paired account with the opposite sign when that leg is cleared too.

This does not weaken the check. The projection cancels exactly the FX spread of rows this run converts, and never touches rows YMCA skips, so E25's hand-edit drift (`already-converted`, so never converted again), split rows, and legacy-marker rows all still surface.

- Unit: [`tests/unit/test_balance.py`](../tests/unit/test_balance.py) — `test_pending_conversion_delta_rebases_the_drift_check`, `test_pending_conversion_delta_does_not_mask_unrelated_drift`; [`tests/unit/test_conversion.py`](../tests/unit/test_conversion.py) — `test_build_prepared_conversion_populates_tracking_for_tracked_account`, `test_build_prepared_conversion_leaves_drift_baseline_alone_for_uncleared_rows`, `test_build_prepared_conversion_rebases_paired_transfer_leg_drift_baseline`.
- Offline workflow: [`tests/workflows/test_offline_workflows.py`](../tests/workflows/test_offline_workflows.py) — `test_new_cleared_transaction_does_not_report_drift_workflow`, `test_hand_edited_converted_row_still_reports_drift_workflow`.
- Integration: [`tests/integration/test_sync_cleared_conversion_drift.py`](../tests/integration/test_sync_cleared_conversion_drift.py) — seeds and converts a cleared row, enters a second cleared row, and asserts the next sync prepares the conversion while staying within tolerance.
