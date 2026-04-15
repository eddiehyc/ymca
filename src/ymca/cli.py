from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .config import load_config, write_config_template
from .conversion import build_prepared_conversion, execute_conversion, resolve_bindings
from .errors import YmcaError
from .memo import format_milliunits
from .paths import default_config_path, default_state_path
from .secrets import load_api_key
from .state import load_state, save_state
from .ynab_client import YnabClient


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        return _dispatch(args)
    except YmcaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "config" and args.config_command == "init":
        return _handle_config_init(path=args.path, force=args.force)
    if args.command == "config" and args.config_command == "check":
        return _handle_config_check(path=args.path)
    if args.command == "discover":
        return _handle_discover()
    if args.command == "convert":
        return _handle_convert(
            account_aliases=tuple(args.account),
            apply_updates=args.apply,
            bootstrap_since=args.bootstrap_since,
        )
    raise RuntimeError(f"Unsupported command: {args}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ymca", description="YNAB FX conversion CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Manage local YMCA config.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_init_parser = config_subparsers.add_parser("init", help="Create a sample config file.")
    config_init_parser.add_argument("--path", type=Path, default=default_config_path())
    config_init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file.",
    )

    config_check_parser = config_subparsers.add_parser(
        "check",
        help="Validate config and secret access.",
    )
    config_check_parser.add_argument("--path", type=Path, default=default_config_path())

    subparsers.add_parser("discover", help="List visible YNAB plans and account names.")

    convert_parser = subparsers.add_parser("convert", help="Convert matching YNAB transactions.")
    convert_parser.add_argument(
        "--account",
        action="append",
        default=[],
        help=(
            "Limit conversion to one configured account alias. Repeat to include multiple accounts."
        ),
    )
    convert_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write converted amounts and FX memos back to YNAB.",
    )
    convert_parser.add_argument(
        "--bootstrap-since",
        type=_parse_date_argument,
        help="Initial sync date to use when no saved server knowledge exists yet.",
    )

    return parser


def _handle_config_init(*, path: Path, force: bool) -> int:
    write_config_template(path, force=force)
    print(f"Wrote config template to {path}")
    return 0


def _handle_config_check(*, path: Path) -> int:
    config = load_config(path)
    api_key = load_api_key(api_key_file=config.secrets.api_key_file)

    print(f"Config path: {path}")
    print("Config schema: OK")
    print("API key: OK")

    with YnabClient(api_key) as gateway:
        bindings = resolve_bindings(config.plan, gateway)

    print("YNAB auth: OK")
    print(f"Plan: OK ({config.plan.name})")
    for account in config.plan.accounts:
        resolved_id = bindings.account_ids.get(account.alias)
        status = "OK" if resolved_id is not None else "MISSING"
        print(f"Account {account.alias}: {status} ({account.name})")

    return 0


def _handle_discover() -> int:
    config_path = default_config_path()
    configured_api_key_file = None
    if config_path.is_file():
        configured_api_key_file = load_config(config_path).secrets.api_key_file

    api_key = load_api_key(api_key_file=configured_api_key_file)
    with YnabClient(api_key) as gateway:
        plans = gateway.list_plans(include_accounts=True)

    if not plans:
        print("No YNAB plans found.")
        return 0

    for plan in plans:
        print(f"Plan: {plan.name}")
        if not plan.accounts:
            print("  Accounts: none returned")
            continue
        for account in plan.accounts:
            if account.deleted:
                continue
            print(f"  - {account.name}")
    return 0


def _handle_convert(
    *,
    account_aliases: tuple[str, ...],
    apply_updates: bool,
    bootstrap_since: date | None,
) -> int:
    config_path = default_config_path()
    state_path = default_state_path()
    config = load_config(config_path)
    state = load_state(state_path)
    api_key = load_api_key(api_key_file=config.secrets.api_key_file)

    with YnabClient(api_key) as gateway:
        prepared = build_prepared_conversion(
            plan=config.plan,
            state=state,
            gateway=gateway,
            selected_account_aliases=account_aliases,
            bootstrap_since=bootstrap_since,
            prompt_for_start_date=_prompt_for_start_date,
        )
        outcome = execute_conversion(
            prepared=prepared,
            state=state,
            gateway=gateway,
            apply_updates=apply_updates,
        )

    if apply_updates:
        save_state(state_path, outcome.new_state)

    _print_conversion_summary(outcome)
    return 0


def _print_conversion_summary(outcome: object) -> None:
    from .models import ConversionOutcome

    if not isinstance(outcome, ConversionOutcome):
        raise RuntimeError("Unexpected conversion result.")

    prepared = outcome.prepared
    mode = "APPLY" if outcome.applied else "DRY RUN"
    print(f"Mode: {mode}")
    if prepared.sync_request.used_bootstrap and prepared.sync_request.since_date is not None:
        print(f"Sync: bootstrap from {prepared.sync_request.since_date.isoformat()}")
    elif prepared.sync_request.last_knowledge_of_server is not None:
        print(f"Sync: last_knowledge_of_server={prepared.sync_request.last_knowledge_of_server}")
    print(f"Fetched transactions: {prepared.fetched_transactions}")
    print(f"Prepared updates: {len(prepared.updates)}")
    print(f"Skipped transactions: {len(prepared.skipped)}")

    for update in prepared.updates:
        source_amount = format_milliunits(update.source_amount_milliunits, places=3)
        converted_amount = format_milliunits(update.converted_amount_milliunits, places=3)
        print(
            f"- {update.date.isoformat()} {update.account_alias}: "
            f"{source_amount} {update.source_currency} -> "
            f"{converted_amount} {update.converted_currency}"
        )
        print(f"  memo: {update.new_memo}")

    if prepared.skipped:
        for skipped in prepared.skipped:
            account_text = skipped.account_alias or "<unknown>"
            print(f"- skipped {skipped.date.isoformat()} {account_text}: {skipped.reason}")

    if outcome.applied:
        print(f"Writes applied: {outcome.writes_performed}")
        if outcome.saved_server_knowledge is not None:
            print(f"Saved server knowledge: {outcome.saved_server_knowledge}")


def _prompt_for_start_date() -> date:
    while True:
        raw_value = input(
            "No saved server knowledge. Enter bootstrap start date (YYYY-MM-DD): "
        ).strip()
        try:
            return date.fromisoformat(raw_value)
        except ValueError:
            print("Invalid date. Please use YYYY-MM-DD.", file=sys.stderr)


def _parse_date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Invalid date {value!r}. Use YYYY-MM-DD.") from exc
