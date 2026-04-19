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

### 3.4 `ymca sync [--account ALIAS]... [--apply] [--bootstrap-since YYYY-MM-DD] [--rebuild-balance]`

- dry-run by default
- writes only when `--apply` is present
- uses saved `server_knowledge` when available
- if `--bootstrap-since` is supplied, it takes precedence for that run and ignores saved `server_knowledge`
- prompts for a bootstrap date if no local `server_knowledge` exists and no bootstrap date is supplied
- saves refreshed `server_knowledge` after successful apply runs
- if `--rebuild-balance` is supplied, switches tracked accounts into full-scan mode (see §12); mutually exclusive with `--bootstrap-since`

## 4. Runtime Path Resolution

Path handling is intentionally split between config-management commands and runtime commands.

### 4.1 Config-Management Commands

`ymca config init --path ...` and `ymca config check --path ...` operate on the explicit path supplied by the user.

They do not change the runtime config path used by later `discover` or `sync` commands.

### 4.2 Runtime Commands

`ymca discover` and `ymca sync` resolve the config path in this order:

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
- conversion math uses the configured `rate` value at full `Decimal` precision (whatever you type in YAML)
- the rate string embedded in new `[FX]` markers from the main converter is the same value rounded to three decimal places (`ROUND_HALF_UP`), then normalized (trailing fractional zeros dropped when possible), matching memo formatting for long rates such as GBP

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
- FX `rate` values from config are not rounded for conversion math; memo markers use a shorter display form for the same rate (see Memo Format)

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
- `[FX] 500 HKD (rate: 0.128 USD/HKD)`
- `[FX] +/-78 HKD (rate: 0.128 USD/HKD)`

Formatting rules:

- source amounts are rounded to 2 decimal places for memo display
- FX rates in new markers from the main converter are rounded to 3 decimal places for memo display (`ROUND_HALF_UP`)
- trailing fractional zeros after the decimal point are trimmed when possible (for both source amounts and rates)
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

The migration helper rewrites legacy memo text into the current `[FX]` structure. It copies the legacy `(FX rate: ...)` numeric substring into the new marker without re-rounding, so migrated memos can still show more than three fractional digits if the old text did.

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
- memo: `[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)` (the existing `[FX]` substring is kept; only the legacy segment is removed)

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

## 12. Local Currency Tracking

Local currency tracking is an **opt-in**, per-account feature layered on top of the FX conversion pipeline. When enabled, `ymca sync` maintains a running source-currency balance (e.g. HKD for an HKD account) for the tracked account and surfaces it through a dedicated YNAB **sentinel transaction** inside that account.

### 12.1 Why a Sentinel Transaction

The YNAB public REST API (OpenAPI 1.79.0 at time of writing) does not expose an endpoint to rename an existing account: `AccountsApi` only supports `create_account`, `get_accounts`, and `get_account_by_id`. Writing the running balance into the account name is therefore not feasible. YMCA instead maintains a single zero-amount sentinel transaction per tracked account whose memo encodes the running local-currency balance. YNAB users can read the balance by viewing that transaction in the register.

### 12.2 Config Toggle

Each account may set `track_local_balance: true` in the config file. Defaults to `false`. The toggle is rejected by schema validation if the account uses the base currency.

```yaml
accounts:
  hsbc_hkd:
    name: HSBC HK [HKD]
    currency: HKD
    enabled: true
    track_local_balance: true
```

### 12.3 Sentinel Transaction Shape

- **Payee name**: `[YMCA] Tracked Balance` (constant; used as the detection key).
- **Amount**: `0` milliunits.
- **Cleared status**: `reconciled` (keeps it out of the "needs clearing" UI bucket).
- **Flag color**: `green`. Makes the sentinel visually distinct in the YNAB register and is re-applied on every sync run, so a hand-cleared flag is restored automatically on the next run.
- **Date**: the date of the last update in the account's local timezone.
- **Memo** (single-line, pipe-separated):

```text
[YMCA-BAL] <CCY> <amount> | rate <rate_text> <PAIR> | updated <ISO8601_UTC> | prev <prev_amount> <prev_ISO8601_UTC> | drift <drift_signed> <stronger_CCY>
```

Example:

```text
[YMCA-BAL] HKD 1,234.56 | rate 7.8 HKD/USD | updated 2026-04-19T14:30:45Z | prev 1,200.00 2026-04-18T14:30:45Z | drift 0.00 USD
```

`prev ...` is omitted on the first write. The amount is rounded to two decimal places, with thousands separators, matching the existing `[FX]` memo style.

The sentinel transaction itself is always excluded from FX conversion and from the running-balance computation (detected by exact payee-name match).

### 12.4 Per-Run Algorithm (Delta Mode)

For every tracked account, during a normal (non-rebuild) `ymca sync` run:

1. Fetch the delta using saved `server_knowledge` (unchanged from the existing flow).
2. For each returned transaction in the account:
   - **Sentinel transaction**: skip.
   - **Already carries `[FX]` or legacy `(FX rate: ...)` marker** (i.e. we have seen this row before):
     - `cleared` or `reconciled`, **not deleted** → **no-op** (assume already counted).
     - `cleared` or `reconciled`, **deleted** → **subtract** the source amount parsed from the memo.
     - `uncleared` → **no-op** (see §12.7 Known Limitations).
   - **Does not yet carry a marker** (new or untouched row):
     - Run the normal FX conversion path (append `[FX]` marker, rewrite amount).
     - `cleared` or `reconciled`, **not deleted** → **add** the local-currency amount to the running balance.
     - `uncleared` → FX convert only; no balance change.
     - `deleted` → skip entirely.
3. Run the tolerance check (§12.6). Emit a warning if drift exceeds the threshold; do not block the run.
4. Upsert the sentinel transaction so its memo reflects the new balance. First enablement creates the sentinel via `create_transaction`; subsequent runs update it via `update_transaction`.

### 12.5 Rebuild Mode (`--rebuild-balance`)

`ymca sync --rebuild-balance` switches all selected tracked accounts into full-scan mode:

- Saved `server_knowledge` is not consulted. The run fetches every active transaction in each tracked account since the earliest available date.
- Each account's running balance is recomputed from scratch:
  - Every non-sentinel, non-deleted, non-split transaction that is `cleared` or `reconciled` **and** carries either the current `[FX]` marker or the legacy `(FX rate: ...)` marker contributes its parsed local-currency amount to the new balance.
  - Unmarked transactions encountered during rebuild are FX-converted by the normal rules; their contribution to the balance follows §12.4.
- The sentinel is upserted with the newly computed balance.
- `--rebuild-balance` is mutually exclusive with `--bootstrap-since`.
- `--rebuild-balance` requires at least one tracked account in scope after the `--account` filter; otherwise the command errors out before contacting YNAB.
- After a successful apply, `server_knowledge` is still refreshed so later delta-mode runs remain correct.

### 12.6 Tolerance Check

For each tracked account, at the end of every sync run, YMCA compares the running balance against YNAB's reported `cleared_balance` (which is in the base currency):

- Determine the **stronger currency**: the base currency when `divide_to_base: true`, the source currency when `divide_to_base: false`.
- Convert both values to the stronger currency.
- If `|tracked − cleared_balance| > 0.01` stronger-currency units, print a warning suggesting `ymca sync --rebuild-balance`.

The warning is informational only. The sync run does not fail.

### 12.7 Sign Inference Rules

- **Default**: use the sign of the YNAB transaction amount (positive = inflow, negative = outflow). When the YNAB amount is non-zero, its sign **overrides** any sign embedded in the memo. This protects the tracked balance from stale or hand-edited memos where, for example, a transfer outflow ended up stamped with a ``+`` in its FX marker: the YNAB-side sign still drives the contribution.
- **Zero-amount non-transfer**: the YNAB amount is `0`; the memo-embedded local amount still carries a sign. Use the memo sign.
- **Zero-amount transfer**: the memo shows `+/-` literal prefix and the YNAB amount is `0`. Direction is ambiguous. Under `--rebuild-balance` the CLI prompts interactively (`(i)n/(o)ut/(s)kip`). Non-TTY plus `--apply` fails fast; dry-run without a TTY surfaces the ambiguous rows in the summary and skips them. The delta-mode sync never encounters this case for a new transaction (it would be converted uncleared), but if one shows up cleared, it is treated like any other 0-amount row per §12.4 with direction coming from the memo sign.

### 12.8 Known Limitations

These cases are explicitly **not supported** by the delta-mode algorithm and will cause the tracked balance to drift. Users recover via the tolerance check and `ymca sync --rebuild-balance`:

- **Modifying an already-converted, cleared or reconciled transaction** (changing the amount or memo). YMCA cannot distinguish this from a new row and does not re-read prior state, so the balance does not update.
- **Transitioning a previously-counted transaction from cleared/reconciled back to uncleared** without deletion. YMCA does not track per-transaction contribution state, so it cannot reverse the original add.

### 12.9 Interaction With Existing Skip Rules

The top-level skip rules from §7.2 still apply to the FX conversion step:

- Sentinel transactions are skipped for FX conversion (they already have a special memo shape).
- Split transactions are still skipped; their local-currency amount does not contribute to the tracked balance.
- Already-marked transactions (current or legacy) still skip FX conversion but are inspected by the balance algorithm as described in §12.4 and §12.5.
