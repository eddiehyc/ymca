# YMCA

`ymca` is a small CLI for converting foreign-currency YNAB transactions into a configured base currency while keeping the original foreign amount in a structured memo marker.

It is designed for a local, privacy-friendly workflow:

- YNAB API keys never need to live in tracked source.
- YNAB UUIDs and sync state live in a local state file, not in your config.
- Normal runs use YNAB delta sync via `last_knowledge_of_server`.
- Core conversion logic is kept separate from the CLI so the project can later grow into a small web app.

## What It Does

- Discovers visible YNAB budgets and open accounts.
- Converts enabled foreign-currency accounts into the configured base currency.
- Works at milliunit precision, not cent precision.
- Appends a deterministic FX memo marker like `[FX] -123.45 HKD (rate: 7.8 HKD/USD)` (the `rate:` value is rounded to three decimal places for the memo; conversion still uses your full configured rate).
- Dry-runs by default and only writes when you pass `--apply`.
- Stores YNAB `server_knowledge` locally after successful apply runs.

## Install

Install `uv` first. On macOS, Homebrew is the simplest option:

```bash
brew install uv
```

Then sync the project:

```bash
uv sync --dev
```

## First-Time Setup

1. Create a config file:

```bash
uv run ymca config init
```

2. Edit the generated config at `~/.config/ymca/config.yaml`, or use `ymca config init --path ...` and `ymca config check --path ...` for a different config file. For runtime commands, use `YMCA_CONFIG_PATH` if you want YMCA to read that non-default file.

3. Provide your YNAB API key in one of these ways:

- Set `YNAB_API_KEY`
- Point `secrets.api_key_file` at a local file that contains only the token
- Let YMCA prompt you for the token when needed

4. Validate the config and YNAB access:

```bash
uv run ymca config check
```

5. Discover the exact YNAB budget and account names you should put into the config:

```bash
uv run ymca discover
```

6. Run a dry conversion preview:

```bash
uv run ymca sync
```

7. Apply the updates once the preview looks right:

```bash
uv run ymca sync --apply
```

## Config File

Default config path:

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
  hsbc_hk_hkd:
    name: HSBC HK HKD
    currency: HKD
    enabled: true
  hsbc_uk_gbp:
    name: HSBC UK GBP
    currency: GBP
    enabled: true

fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
  GBP:
    rate: "1.35"
    divide_to_base: false
```

Rules:

- In the YAML, `plan` refers to the YNAB budget.
- `plan.name` must exactly match the YNAB budget name.
- Each account `name` must exactly match the YNAB account name.
- Account aliases are your local labels; they do not need to match YNAB.
- `rate` must be greater than `1`.
- `divide_to_base: true` means `base = source / rate`, for example `7.8 HKD/USD`.
- `divide_to_base: false` means `base = source * rate`, for example `1.35 USD/GBP`.
- Only enabled foreign-currency accounts are converted.

## Runtime Paths And Environment Variables

YMCA has two different path behaviors, which is the main thing to keep in mind when you use a non-default config or state file.

`ymca config init --path ...` and `ymca config check --path ...` operate on the specific file path you give them.

`ymca discover` and `ymca sync` do not take a config `--path` flag. They use the runtime path resolution below:

- `YMCA_CONFIG_PATH`, if set
- otherwise `~/.config/ymca/config.yaml`

State uses:

- `YMCA_STATE_PATH`, if set
- otherwise `~/.local/state/ymca/state.yaml`

Secrets use:

- `YNAB_API_KEY`, if set
- otherwise `secrets.api_key_file` from the config
- otherwise an interactive prompt

Examples:

```bash
YMCA_CONFIG_PATH=~/work/ymca/config.yaml uv run ymca discover
YMCA_CONFIG_PATH=~/work/ymca/config.yaml YMCA_STATE_PATH=~/work/ymca/state.yaml uv run ymca sync --apply
uv run ymca config check --path ~/work/ymca/config.yaml
```

## Everyday Commands

Discover budgets and accounts:

```bash
uv run ymca discover
```

This lists visible YNAB budgets and open accounts only. Closed and deleted accounts are hidden.

Check config and credentials:

```bash
uv run ymca config check
```

Dry-run sync:

```bash
uv run ymca sync
```

Apply sync:

```bash
uv run ymca sync --apply
```

Limit the sync to one or more configured account aliases:

```bash
uv run ymca sync --account hsbc_hk_hkd --account hsbc_uk_gbp
```

Bootstrap from a specific date:

```bash
uv run ymca sync --bootstrap-since 2025-01-01
```

If you pass `--bootstrap-since`, it takes precedence for that run and ignores saved `server_knowledge`.

## Sync And State

Default state path:

```text
~/.local/state/ymca/state.yaml
```

State stores:

- resolved YNAB budget and account IDs
- the last saved `server_knowledge` per configured plan alias

Normal `sync` runs use saved `server_knowledge` when present. On a first run with no saved knowledge, YMCA asks for a bootstrap start date unless you provide `--bootstrap-since`.

Dry runs do not save state. Successful `--apply` runs do save state.

## Conversion Behavior

- Conversion uses YNAB milliunits and rounds to the nearest milliunit.
- Example: `12340` means `12.34`. With `7.8 HKD/USD`, YMCA uploads `1582`, not `1580`.
- The current FX marker format is `[FX] -123.45 HKD (rate: 7.8 HKD/USD)`.
- The `rate:` in that marker is shown with up to three decimal places (half up), then trailing fractional zeros are dropped when possible; uploads still use the full-precision rate from config.
- Existing memos keep their text and get the FX marker appended at the end.
- Transfers are converted too, and transfer markers use a literal `+/-` amount prefix.
- Split transactions are skipped by the main converter.
- Transactions that already contain either the current FX marker or the old legacy FX marker are skipped.

## Deprecated One-Off Helpers

These are not part of the supported YMCA CLI surface. They are only kept for manual repair and investigation work.

Preferred location:

```bash
uv run python deprecated/one_off_scripts/get_account_delta.py --last-server-knowledge 123
uv run python deprecated/one_off_scripts/migrate_legacy_fx_memos.py
uv run python deprecated/one_off_scripts/migrate_legacy_fx_memos.py --apply
uv run python deprecated/one_off_scripts/fix_double_converted_transactions.py
uv run python deprecated/one_off_scripts/fix_double_converted_transactions.py --apply
```

## Local Checks

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests deprecated
uv run pytest
```
