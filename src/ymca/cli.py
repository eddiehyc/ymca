from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from .balance import TransferDirectionPrompt
from .config import load_config, write_config_template
from .conversion import build_prepared_conversion, execute_conversion, resolve_bindings
from .errors import UserInputError, YmcaError
from .memo import format_balance_milliunits, format_milliunits
from .models import AmbiguousTransfer
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
    if args.command == "sync":
        return _handle_sync(
            account_aliases=tuple(args.account),
            apply_updates=args.apply,
            bootstrap_since=args.bootstrap_since,
            rebuild_balance=args.rebuild_balance,
        )
    raise RuntimeError(f"Unsupported command: {args}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ymca",
        description=(
            "YNAB FX conversion CLI. Runtime commands read YMCA_CONFIG_PATH and "
            "YMCA_STATE_PATH when set."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Manage local YMCA config.")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)

    config_init_parser = config_subparsers.add_parser(
        "init",
        help="Create a sample config file.",
    )
    config_init_parser.add_argument(
        "--path",
        type=Path,
        default=default_config_path(),
        help=(
            "Write the template to this path. This does not change the runtime path used by "
            "discover or sync."
        ),
    )
    config_init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config file.",
    )

    config_check_parser = config_subparsers.add_parser(
        "check",
        help="Validate a config file and secret access.",
    )
    config_check_parser.add_argument(
        "--path",
        type=Path,
        default=default_config_path(),
        help=(
            "Validate this config file. This does not change the runtime path used by "
            "discover or sync."
        ),
    )

    subparsers.add_parser(
        "discover",
        help="List visible YNAB plans and open account names from the runtime config path.",
    )

    sync_parser = subparsers.add_parser(
        "sync",
        help="Sync YNAB: convert foreign-currency transactions to the base currency.",
    )
    sync_parser.add_argument(
        "--account",
        action="append",
        default=[],
        help=(
            "Limit the sync to one configured account alias. Repeat to include multiple accounts."
        ),
    )
    sync_parser.add_argument(
        "--apply",
        action="store_true",
        help="Write converted amounts and FX memos back to YNAB.",
    )
    mode_group = sync_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--bootstrap-since",
        type=_parse_date_argument,
        help="Sync from this date and ignore saved server knowledge for the current run.",
    )
    mode_group.add_argument(
        "--rebuild-balance",
        action="store_true",
        help=(
            "Rebuild the tracked local-currency balance for every tracked account in scope "
            "by scanning every active transaction and re-parsing FX markers. Mutually "
            "exclusive with --bootstrap-since."
        ),
    )

    return parser


def _handle_config_init(*, path: Path, force: bool) -> int:
    write_config_template(path, force=force)
    print(f"Wrote config template to {path}")
    if path != default_config_path():
        print(
            "Note: discover and sync still use YMCA_CONFIG_PATH or the default config path "
            "unless you set YMCA_CONFIG_PATH."
        )
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

    if path != default_config_path():
        print(
            "Note: discover and sync still use YMCA_CONFIG_PATH or the default config path "
            "unless you set YMCA_CONFIG_PATH."
        )

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
            if account.deleted or account.closed:
                continue
            print(f"  - {account.name}")
    return 0


def _handle_sync(
    *,
    account_aliases: tuple[str, ...],
    apply_updates: bool,
    bootstrap_since: date | None,
    rebuild_balance: bool,
) -> int:
    config_path = default_config_path()
    state_path = default_state_path()
    config = load_config(config_path)
    state = load_state(state_path)
    api_key = load_api_key(api_key_file=config.secrets.api_key_file)

    prompt_for_transfer = _build_transfer_direction_prompt(
        apply_updates=apply_updates,
    )

    with YnabClient(api_key) as gateway:
        prepared = build_prepared_conversion(
            plan=config.plan,
            state=state,
            gateway=gateway,
            selected_account_aliases=account_aliases,
            bootstrap_since=bootstrap_since,
            prompt_for_start_date=_prompt_for_start_date,
            rebuild_balance=rebuild_balance,
            prompt_for_transfer_direction=prompt_for_transfer,
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


def _build_transfer_direction_prompt(*, apply_updates: bool) -> TransferDirectionPrompt:
    """Return a callback that resolves 0-amount-transfer directions.

    Interactive prompt on a TTY. Non-TTY + apply: fail fast. Non-TTY dry-run:
    mark as ambiguous (return None) so the summary surfaces the rows.
    """

    def prompt(event: AmbiguousTransfer) -> int | None:
        if not sys.stdin.isatty():
            if apply_updates:
                raise UserInputError(
                    "Zero-amount transfer direction is ambiguous for transaction "
                    f"{event.transaction_id} (account {event.account_alias}). "
                    "Re-run on a TTY, or drop --apply, to resolve."
                )
            return None
        while True:
            raw = input(
                f"\nZero-amount transfer in account {event.account_alias} "
                f"(txn {event.transaction_id}, date {event.date.isoformat()}): "
                f"magnitude {format_balance_milliunits(event.memo_amount_milliunits)} "
                f"{event.currency}. (i)n / (o)ut / (s)kip? "
            ).strip().lower()
            if raw in {"i", "in"}:
                return 1
            if raw in {"o", "out"}:
                return -1
            if raw in {"s", "skip"}:
                return None
            print("Please enter 'i', 'o', or 's'.", file=sys.stderr)

    return prompt


def _print_conversion_summary(outcome: object) -> None:
    from .models import ConversionOutcome

    if not isinstance(outcome, ConversionOutcome):
        raise RuntimeError("Unexpected conversion result.")

    prepared = outcome.prepared
    mode = "APPLY" if outcome.applied else "DRY RUN"
    print(f"Mode: {mode}")
    if prepared.rebuild_balance:
        print("Balance mode: REBUILD (full scan)")
    if prepared.sync_request.used_bootstrap and prepared.sync_request.since_date is not None:
        print(f"Sync: bootstrap from {prepared.sync_request.since_date.isoformat()}")
    elif prepared.sync_request.last_knowledge_of_server is not None:
        print(f"Sync: last_knowledge_of_server={prepared.sync_request.last_knowledge_of_server}")
    elif prepared.rebuild_balance:
        print("Sync: full scan (no since_date, no server_knowledge)")
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

    if prepared.tracking:
        print("")
        print("Local currency tracking:")
        for entry in prepared.tracking:
            prior_text = format_balance_milliunits(entry.prior_balance_milliunits)
            new_text = format_balance_milliunits(entry.new_balance_milliunits)
            delta = entry.new_balance_milliunits - entry.prior_balance_milliunits
            delta_text = format_balance_milliunits(delta)
            sentinel_suffix = ""
            if entry.create_sentinel is not None:
                sentinel_suffix = "; sentinel: create"
            elif entry.update_sentinel is not None:
                sentinel_suffix = "; sentinel: update"
            print(
                f"- {entry.account_alias} ({entry.currency}): "
                f"{prior_text} -> {new_text} (delta {delta_text})"
                f"{sentinel_suffix}"
            )
            if entry.contributions:
                print(f"  contributions: {len(entry.contributions)} row(s)")
            if entry.ambiguous_transfers:
                print(
                    f"  ambiguous 0-amount transfers (skipped): "
                    f"{len(entry.ambiguous_transfers)} row(s)"
                )
                for ambiguous in entry.ambiguous_transfers:
                    print(
                        f"    - {ambiguous.date.isoformat()} {ambiguous.transaction_id} "
                        f"({ambiguous.currency})"
                    )
            drift_text = format_balance_milliunits(entry.drift_milliunits_stronger)
            if entry.within_tolerance:
                print(
                    f"  drift check: {drift_text} {entry.stronger_currency} "
                    "(within tolerance)"
                )
            else:
                print(
                    f"  drift check: {drift_text} {entry.stronger_currency} "
                    "(DRIFT beyond 0.01; run `ymca sync --rebuild-balance` to recover)"
                )

    if outcome.applied:
        print(f"Writes applied: {outcome.writes_performed}")
        if outcome.sentinel_writes:
            created_text = (
                f" ({outcome.sentinels_created} created)"
                if outcome.sentinels_created
                else ""
            )
            print(f"Sentinel writes: {outcome.sentinel_writes}{created_text}")
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
