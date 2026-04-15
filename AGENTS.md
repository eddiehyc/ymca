# AGENTS.md

## Project Shape

- This repo is a `uv`-managed Python CLI project.
- Runtime code lives under `src/ymca`.
- The CLI is only one layer; core conversion logic must stay reusable for a future small web app.

## Safety Rules

- Never hardcode or commit YNAB API keys.
- Never hardcode or commit YNAB plan IDs, account IDs, or other identifying UUIDs.
- Config examples, docs, and tests must only use placeholders or fake values.
- Local YNAB IDs and sync state belong in the local state file, not tracked source files.

## Code Standards

- Keep the codebase strongly typed.
- Prefer immutable typed models for business logic over loose dicts.
- Keep YNAB SDK usage inside the adapter layer; map SDK models into internal models before business logic touches them.
- Preserve the append-style FX memo format and milliunit-precision conversion behavior unless the spec changes.

## Required Checks

- Run `uv sync --dev` when dependencies or tooling change.
- Run `uv run ruff check .`
- Run `uv run mypy src tests`
- Run `uv run pytest`

## Docs Discipline

- Update `docs/spec.md` when the CLI contract, config schema, local state schema, sync behavior, or memo format changes.
- Update tests whenever conversion math, config validation, or YNAB adapter behavior changes.
