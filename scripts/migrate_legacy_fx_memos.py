from __future__ import annotations

import argparse
import sys
from pathlib import Path

from scripts._deprecation import print_deprecation_warning
from scripts._legacy_memo_migration import (
    apply_legacy_memo_migration_plan,
    build_legacy_memo_migration_plan,
)
from ymca.config import load_config
from ymca.errors import YmcaError
from ymca.paths import default_config_path
from ymca.secrets import load_api_key
from ymca.ynab_client import YnabClient


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
