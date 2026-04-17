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

## Testing Requirements
### Unit Test
- Full unit test suite that achieve 90% line covreage
- Mock external interactions when necessary

### Integration Test
- There is a YNAB plan created just for integration test -- you can use that plan and YNAB SDK for integration test
    - name: "_Intergration Test_ USE ONLY"
    - use get_plans in PlansAPI to get the plan_id for that plan
    - API Key should be provided as YNAB_API_KEY env var -- prompt user if not provided
- DO NOT modify any data outside of "_Intergration Test_ USE ONLY" test YNAB plan
    - In other words, any write operation(CREATE/UPDATE/DELETE) should only happen within "_Intergration Test_ USE ONLY" plan 
- Clean that plan before integration test and restore the plan to a clean state after the test
- Integration test is required for all workflows and all edge cases, both documented and newly discovered
    - Some edge cases:
        - 0 amount(both pre & post conversion) transactions
        - transfer transactions
        - transactions with split categories
        - transfer transaction with split categories
- Workflows and edge cases should be documented in docs/ directory
    - for new workflows/edge cases that are discovered, MUST document them

## Required Checks

- Run `uv sync --dev` when dependencies or tooling change.
- Run `uv run ruff check .`
- Run `uv run mypy src tests`
- Run `uv run pytest`

## Docs Discipline

- Update `docs/spec.md` when the CLI contract, config schema, local state schema, sync behavior, or memo format changes.
- Update tests whenever conversion math, config validation, or YNAB adapter behavior changes.
