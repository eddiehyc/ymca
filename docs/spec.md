# YMCA Specification

## 1. Overview

`ymca` is a typed Python CLI for converting foreign-currency YNAB transactions into a configured base currency and updating those transactions in place.

The product is intentionally local-first:

- secrets stay out of tracked source
- YNAB UUIDs stay out of tracked config
- sync state is stored locally
- the core conversion engine is separate from the CLI

## 2. Scope

### 2.1 Goals

- Use the official YNAB Python SDK.
- Use `uv` for project management.
- Keep the codebase strongly typed.
- Keep config human-edited and identifier-free.
- Persist YNAB delta sync state locally.
- Preserve milliunit precision when converting and uploading amounts.
- Append a deterministic FX memo marker for idempotency.
- Keep the conversion engine reusable for a later web application.

### 2.2 Non-Goals

- Automatic FX-rate fetching
- Historical FX rates
- Split-transaction conversion in the main CLI
- A web UI in v1

## 3. Supported CLI Surface

### 3.1 `ymca config init [--path PATH] [--force]`

Creates a placeholder config file with no secrets and no YNAB IDs.

### 3.2 `ymca config check [--path PATH]`

Performs an online validation pass:

- validates config schema
- confirms an API key is available
- verifies YNAB authentication
- resolves the configured budget name
- resolves configured account names

### 3.3 `ymca discover`

Lists visible YNAB budgets and open accounts to help the user fill in the config file.

Closed and deleted accounts are not shown.

### 3.4 `ymca convert [--account ALIAS]... [--apply] [--bootstrap-since YYYY-MM-DD]`

- dry-run by default
- writes only when `--apply` is present
- uses saved `server_knowledge` when available
- if `--bootstrap-since` is supplied, it takes precedence for that run and ignores saved `server_knowledge`
- prompts for a bootstrap date if no local `server_knowledge` exists and no bootstrap date is supplied
- saves refreshed `server_knowledge` after successful apply runs

## 4. Runtime Path Resolution

Path handling is intentionally split between config-management commands and runtime commands.

### 4.1 Config-Management Commands

`ymca config init --path ...` and `ymca config check --path ...` operate on the explicit path supplied by the user.

They do not change the runtime config path used by later `discover` or `convert` commands.

### 4.2 Runtime Commands

`ymca discover` and `ymca convert` resolve the config path in this order:

1. `YMCA_CONFIG_PATH`
2. `~/.config/ymca/config.yaml`

They resolve the state path in this order:

1. `YMCA_STATE_PATH`
2. `~/.local/state/ymca/state.yaml`

API key resolution order:

1. `YNAB_API_KEY`
2. `secrets.api_key_file`
3. interactive prompt

## 5. Local Files

### 5.1 Config File

Default path:

```text
~/.config/ymca/config.yaml
```

Example:

```yaml
version: 1
secrets:
  api_key_file: ~/.config/ymca/ynab_api_key

plan:
  alias: personal
  name: Example YNAB Budget Name
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

In the config schema, `plan` refers to the YNAB budget.

Validation rules:

- `version` must be `1`
- `plan.alias`, `plan.name`, and account aliases must be non-empty strings
- `plan.name` must exactly match the YNAB budget name
- Each configured account `name` must exactly match the YNAB account name
- `plan.base_currency` and account `currency` values must be 3-letter uppercase currency codes
- at least one account must be configured
- at least one account must be enabled
- enabled accounts must not use the base currency
- every enabled account currency must have an `fx_rates` entry
- every FX `rate` must be greater than `1`
- `divide_to_base` must be a boolean

FX semantics:

- `divide_to_base: true` means `base = source / rate`
- `divide_to_base: false` means `base = source * rate`
- if base is `USD` and source is `HKD`, store `7.8` with `divide_to_base: true`
- if base is `USD` and source is `GBP`, store `1.35` with `divide_to_base: false`

### 5.2 State File

Default path:

```text
~/.local/state/ymca/state.yaml
```

Example:

```yaml
version: 1
plans:
  personal:
    plan_id: 00000000-0000-0000-0000-000000000000
    account_ids:
      hkd_wallet: 11111111-1111-1111-1111-111111111111
    server_knowledge: 42
```

The state file is local-only and must not be committed.

It stores:

- resolved YNAB budget IDs
- resolved YNAB account IDs
- `server_knowledge` per configured plan alias from the config file

## 6. Sync Model

- YNAB delta sync is the default model.
- Normal runs call YNAB with `last_knowledge_of_server` when saved state exists.
- First-time runs use a bootstrap date supplied through `--bootstrap-since` or an interactive prompt.
- Dry runs do not persist state.
- Successful apply runs persist refreshed `server_knowledge`.
- If writes were performed, YMCA performs a follow-up delta fetch and saves the post-write `server_knowledge`.

## 7. Conversion Semantics

### 7.1 Selection Rules

- process enabled configured accounts only
- fetch transactions account-by-account
- process linked transfers only once per pair

### 7.2 Skip Rules

The main CLI skips:

- deleted transactions
- split transactions
- transactions already containing the current `[FX]` marker
- transactions containing the legacy `(... FX rate: ...)` marker

### 7.3 Amount Precision

- YNAB amounts are treated as milliunits
- conversion uses `Decimal`
- uploads are rounded to the nearest milliunit

Example:

- YNAB amount `12340` means `12.34`
- with `7.8 HKD/USD`, YMCA uploads `1582`
- it does not round to `1580`

### 7.4 Transfer Handling

- transfer transactions are converted
- transfer memo amounts use a literal `+/-` prefix
- account-by-account fetching prevents both sides of a transfer pair from being converted twice in the same run

## 8. Memo Format

The current FX marker is appended to the end of the memo.

Examples:

- `Dinner | [FX] -123.45 HKD (rate: 7.8 HKD/USD)`
- `[FX] 500 HKD (rate: 0.12821 USD/HKD)`
- `[FX] +/-78 HKD (rate: 0.12821 USD/HKD)`

Formatting rules:

- source amounts are rounded to 2 decimal places for memo display
- trailing zeros after the decimal point are trimmed when possible
- thousands separators are used when applicable
- non-transfer positives show no sign
- non-transfer negatives show `-`
- transfers show a literal `+/-`

Detection rule:

- any memo containing the structured `[FX] ... (rate: ...)` marker is treated as already converted

## 9. Legacy Memo Compatibility

Legacy memo text may look like:

- `12.34 HKD (FX rate: 7.8)`
- `78 HKD (FX rate: 0.12821)`
- `-45,586.69 HKD (FX rate: 0.12821)`
- `-45,586.69 HKD (FX rate: 0.12821) · FPS`
- `-/+78 HKD (FX rate: 0.12821)`
- `+/-78 HKD (FX rate: 0.12821)`

The main CLI does not migrate this format. It skips those transactions so current conversion logic stays simple and idempotent.

## 10. Deprecated One-Off Helpers

Deprecated helpers are retained only for manual migration, repair, and debugging work.

Preferred path:

- `deprecated/one_off_scripts/migrate_legacy_fx_memos.py`
- `deprecated/one_off_scripts/get_account_delta.py`
- `deprecated/one_off_scripts/fix_double_converted_transactions.py`

Compatibility note:

- these helpers are not part of the supported `ymca` CLI surface
- their logic should remain confined to the deprecated helper area rather than complicating the main CLI

### 10.1 Legacy Memo Migration

The migration helper rewrites legacy memo text into the current `[FX]` structure.

Examples:

- `12.34 HKD (FX rate: 7.8)` becomes `[FX] 12.34 HKD (rate: 7.8 USD/HKD)`
- `-45,586.69 HKD (FX rate: 0.12821) · FPS` becomes `FPS | [FX] -45,586.69 HKD (rate: 0.12821 USD/HKD)`

Behavior:

- dry-run by default
- uses one bulk `update_transactions` call per account during apply runs where possible
- fetches transaction detail before preparing writes
- can rewrite split parent memos through the single-transaction update path
- preserves the legacy rate direction already encoded in the old memo

### 10.2 Account Delta Inspection

The account delta helper:

- reads configured account aliases from the config file
- fetches `get_transactions_by_account` per account using a supplied `last_knowledge_of_server`
- prints changed transactions per account
- prints requested and returned server knowledge values

### 10.3 Double Conversion Repair

The repair helper targets transactions previously converted twice by older tooling.

Example broken record:

- amount: `78.52`
- memo: `612.49 HKD (FX rate: 0.12821) · [FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)`

Repaired result:

- amount: `612.49`
- memo: `[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)`

Behavior:

- dry-run by default
- fixes only transactions whose amount pattern matches a double conversion
- allows a small milliunit tolerance for older cent-rounded data
- uses one bulk `update_transactions` call per account during apply runs

## 11. Quality Gates

Canonical local check flow:

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests deprecated
uv run pytest
```
