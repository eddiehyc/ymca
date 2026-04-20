# ruff: noqa: E402
from __future__ import annotations

if __package__ in {None, ""}:
    import sys
    from pathlib import Path

    REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

import argparse
import sys
from pathlib import Path

from deprecated.one_off_scripts._deprecation import print_deprecation_warning
from deprecated.one_off_scripts._legacy_memo_migration import (
    apply_legacy_memo_migration_plan,
    build_legacy_memo_migration_plan,
)
from deprecated.one_off_scripts._shared import (
    YmcaError,
    YnabClient,
    default_config_path,
    load_api_key,
    load_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    print_deprecation_warning("migrate_legacy_fx_memos.py")

    try:
        config = load_config(args.config)
        api_key = load_api_key(api_key_file=config.secrets.api_key_file)
        account_aliases = args.account or [
            account.alias for account in config.plan.accounts if account.enabled
        ]

        scanned_transactions = 0
        applied_writes = 0
        prepared_updates = 0

        with YnabClient(api_key) as gateway:
            bindings = None
            for account_alias in account_aliases:
                plan = build_legacy_memo_migration_plan(
                    plan=config.plan,
                    gateway=gateway,
                    selected_account_aliases=(account_alias,),
                    bindings=bindings,
                )
                bindings = plan.bindings
                scanned_transactions += plan.scanned_transactions
                prepared_updates += len(plan.updates)
                for update in plan.updates:
                    print(f"- {update.date.isoformat()} {update.account_alias}: {update.old_memo}")
                    print(f"  -> {update.new_memo}")
                if args.apply:
                    applied_writes += apply_legacy_memo_migration_plan(gateway=gateway, plan=plan)

        mode = "APPLY" if args.apply else "DRY RUN"
        print(f"Mode: {mode}")
        print(f"Scanned transactions: {scanned_transactions}")
        print(f"Prepared memo migrations: {prepared_updates}")
        if args.apply:
            print(f"Writes applied: {applied_writes}")
        return 0
    except YmcaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="migrate_legacy_fx_memos.py",
        description="Migrate legacy FX memo text to the current [FX] marker format.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config_path(),
        help="Path to the YMCA config file.",
    )
    parser.add_argument(
        "--account",
        action="append",
        default=[],
        help=(
            "Limit migration to one configured account alias. "
            "Repeat to include multiple accounts."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write migrated memo text back to YNAB.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
