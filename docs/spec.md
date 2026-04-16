# YMCA v1 Spec

## Summary

`ymca` is a typed Python CLI that converts foreign-currency YNAB transactions into the plan base currency and updates those transactions in place. It uses YNAB delta sync via `last_knowledge_of_server` and stores local sync state outside the repo.

## Goals

- Use the official YNAB Python SDK.
- Keep secrets and identifying UUIDs out of tracked source.
- Use local YAML config and local YAML state.
- Preserve milliunit precision when uploading converted amounts.
- Append a deterministic FX memo marker for idempotency.
- Keep the core conversion logic reusable for a future web app.

## Non-Goals

- Automatic FX-rate fetching
- Date-based FX-rate history
- Split-transaction conversion in the main CLI
- Web UI or phone-specific frontend work in v1

## Supported CLI

### `ymca config init [--path PATH] [--force]`

Creates a placeholder config file without secrets or UUIDs.

### `ymca config check [--path PATH]`

- Validates YAML schema
- Confirms an API key is available
- Verifies YNAB auth
- Confirms configured plan and account names resolve

### `ymca discover`

Lists visible YNAB plan names and account names to help fill config.

### `ymca convert [--account ALIAS]... [--apply] [--bootstrap-since YYYY-MM-DD]`

- Dry-run by default
- Uses saved `server_knowledge` when available
- If `--bootstrap-since` is supplied, it takes precedence for that run and ignores saved `server_knowledge`
- Prompts for a bootstrap date if no `server_knowledge` exists and no `--bootstrap-since` is supplied
- Writes updates only when `--apply` is present
- Saves refreshed `server_knowledge` after successful apply runs
- Fetches transactions account-by-account and processes linked transfers only once per pair
- Applies writes in one bulk `update_transactions` call per configured account to reduce YNAB API request volume

## Deprecated One-Off Scripts

These scripts are kept only for compatibility and manual repair work. They are deprecated and are not part of the supported YMCA CLI workflow.

### `uv run python scripts/migrate_legacy_fx_memos.py [--config PATH] [--account ALIAS]... [--apply]`

- One-time helper for rewriting legacy memo text in existing transactions
- Reads the same YMCA YAML config and secret sources as the CLI
- Dry-run by default
- When `--apply` is used, processes configured accounts one at a time so later account fetches see any transfer-side memo changes from earlier writes
- Uses one bulk `update_transactions` call per account during apply runs to reduce the chance of YNAB rate limiting
- Fetches transaction detail before preparing a write
- Uses the single-transaction update endpoint for split parents so the main memo can be rewritten without going through the bulk patch path for that transaction shape

### `uv run python scripts/get_account_delta.py --last-server-knowledge N [--config PATH] [--account ALIAS]...`

- Read-only helper for debugging YNAB delta sync behavior
- Reads the same YMCA YAML config and secret sources as the CLI
- Resolves configured account aliases from the config file
- Calls YNAB `get_transactions_by_account` for each selected account with the supplied `last_knowledge_of_server`
- Prints changed transactions per account, plus the requested and returned server knowledge values

### `uv run python scripts/fix_double_converted_transactions.py [--config PATH] [--account ALIAS]... [--apply]`

- One-time helper for repairing transactions that were converted twice by older tooling
- Looks for transactions that contain both a legacy FX marker and a current `[FX]` marker
- Fixes only transactions whose current amount exactly matches converting the legacy amount one more time
- Allows a small milliunit tolerance when matching these records so older cent-rounded double conversions are still repairable
- Restores the once-converted amount, removes the legacy marker from the memo, and keeps the current `[FX]` marker
- Dry-run by default
- Uses one bulk `update_transactions` call per account during apply runs

## Config Schema

Default path: `~/.config/ymca/config.yaml`

```yaml
version: 1
secrets:
  api_key_file: ~/.config/ymca/ynab_api_key

plan:
  alias: personal
  name: Example YNAB Plan Name
  base_currency: USD

accounts:
  hkd_wallet:
    name: Example HKD Account
    currency: HKD
    enabled: true

fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
```

### FX Rule Semantics

- `rate` must always be greater than `1`
- `divide_to_base: true` means `base = source / rate`
- `divide_to_base: false` means `base = source * rate`
- Example:
  - If base is `USD` and source is `HKD`, store `7.8` with `divide_to_base: true`
  - If base is `USD` and source is `GBP`, store `1.35` with `divide_to_base: false`

### Secrets

- `secrets.api_key_file` is optional
- The file should contain only the YNAB API token
- Relative paths are resolved relative to the config file directory
- `YNAB_API_KEY` still takes precedence if it is set

## Local State Schema

Default path: `~/.local/state/ymca/state.yaml`

```yaml
version: 1
plans:
  personal:
    plan_id: 00000000-0000-0000-0000-000000000000
    account_ids:
      hkd_wallet: 11111111-1111-1111-1111-111111111111
    server_knowledge: 42
```

This file is local-only and must not be committed.

## Conversion Rules

- The source transaction amount comes from YNAB milliunits
- Conversion is done at milliunit precision, not 2-decimal display precision
- Example:
  - YNAB amount `12340` means `12.34`
  - With `HKD/USD 7.8`, upload `12340 / 7.8 = 1582.05...`, rounded to `1582`
- Transfers are converted too
- Linked transfer pairs are fetched account-by-account and only one side is updated during conversion to avoid double-applying the amount change
- Skip:
  - deleted transactions
  - split transactions
  - already-converted transactions
    - this includes both the current `[FX]` format and the legacy `(... FX rate: ...)` format
- Log skipped transactions with clear reasons

## Memo Format

The current FX marker is always appended to the memo.

- If memo exists:
  - `Dinner | [FX] -123.23 HKD (rate: 7.8 HKD/USD)`
- If memo is empty:
  - `[FX] -123.23 HKD (rate: 7.8 HKD/USD)`

Rules:

- Source amount in the memo is rounded to 2 decimal places, then trailing zeros after the decimal point are trimmed when possible
- Examples:
  - `1234.56` becomes `1,234.56`
  - `1234.50` becomes `1,234.5`
  - `1234.00` becomes `1,234`
  - `0.00` becomes `0`
- Amounts in the memo use thousands separators when applicable
- Non-transfer memos show `-` for negative amounts and no sign for positive or zero amounts
- Transfer memos always use a literal `+/-` prefix before the magnitude, for example `[FX] +/-12.34 HKD (rate: 7.8 HKD/USD)`
- Detection is based on the structured `[FX] ... (rate: ...)` marker anywhere in the memo

## Legacy Memo Format

Old memo text may look like:

- `12.34 HKD (FX rate: 7.8)`
- `78 HKD (FX rate: 0.12821)`
- `78.1 HKD (FX rate: 0.12821)`
- `-45,586.69 HKD (FX rate: 0.12821)`
- `-45,586.69 HKD (FX rate: 0.12821) · FPS`
- `-/+78 HKD (FX rate: 0.12821)`
- `+/-78 HKD (FX rate: 0.12821)`

## Legacy Memo Migration

The deprecated migration script rewrites that text in place to the current `[FX] ... (rate: ...)` structure.

- `12.34 HKD (FX rate: 7.8)` becomes `[FX] 12.34 HKD (rate: 7.8 USD/HKD)`
- `78 HKD (FX rate: 0.12821)` becomes `[FX] 78 HKD (rate: 0.12821 USD/HKD)`
- `-45,586.69 HKD (FX rate: 0.12821) · FPS` becomes `FPS | [FX] -45,586.69 HKD (rate: 0.12821 USD/HKD)`
- The migration preserves the legacy rate direction, so the appended pair label remains `base/source` to match the numeric rate already stored in the old memo text
- The migration normalizes legacy amount text into grouped display format while preserving any decimal digits and any legacy `-/+` or `+/-` transfer prefixes
- Split transactions are not converted by the main CLI, but the deprecated migration script can still rewrite a split parent memo by sending a memo-only single-transaction update

## Double Conversion Repair

Some older runs may have converted already-converted transactions again. A typical broken record looks like:

- Amount: `78.52`
- Memo: `612.49 HKD (FX rate: 0.12821) · [FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)`

The deprecated repair script rewrites that to:

- Amount: `612.49`
- Memo: `[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)`

The main converter also skips transactions with legacy FX markers so this older failure mode is not reintroduced by current runs.

## Checks

Canonical local check flow:

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests
uv run pytest
```
