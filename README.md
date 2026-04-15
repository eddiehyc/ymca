# YMCA

`ymca` is a small CLI for converting foreign-currency YNAB transactions into a plan's base currency.

## Quick Start

```bash
uv sync --dev
uv run ymca config init
# either export YNAB_API_KEY=your-token-here
# or point `secrets.api_key_file` at a local file containing only the token
uv run ymca config check
uv run ymca discover
uv run ymca convert
uv run ymca convert --apply
# one-time legacy memo migration, dry-run first
uv run python scripts/migrate_legacy_fx_memos.py
uv run python scripts/migrate_legacy_fx_memos.py --apply
# one-time repair for transactions that were converted twice
uv run python scripts/fix_double_converted_transactions.py
uv run python scripts/fix_double_converted_transactions.py --apply
```

## Local Checks

```bash
uv sync --dev
uv run ruff check .
uv run mypy src tests
uv run pytest
```

## Notes

- Secrets are never stored in source control.
- `YNAB_API_KEY` takes precedence over the configured API key file.
- Resolved YNAB UUIDs and `server_knowledge` live in a local state file outside the repo by default.
- Transfer transactions are converted too, with an explicit `+` or `-` in the FX marker amount.
- Core conversion logic is kept separate from the CLI so it can be reused by a future web app.
