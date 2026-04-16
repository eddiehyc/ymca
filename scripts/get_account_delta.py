from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ymca.account_delta import build_account_delta_report
from ymca.config import load_config
from ymca.errors import YmcaError
from ymca.memo import format_milliunits
from ymca.paths import default_config_path
from ymca.secrets import load_api_key
from ymca.ynab_client import YnabClient


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        api_key = load_api_key(api_key_file=config.secrets.api_key_file)
        account_aliases = args.account or [
            account.alias for account in config.plan.accounts if account.enabled
        ]

        with YnabClient(api_key) as gateway:
            report = build_account_delta_report(
                plan=config.plan,
                gateway=gateway,
                selected_account_aliases=account_aliases,
                last_knowledge_of_server=args.last_server_knowledge,
            )

        print(f"Requested last_knowledge_of_server: {report.requested_last_knowledge_of_server}")
        print(f"Returned server knowledge: {report.returned_server_knowledge}")
        print(f"Fetched changed transactions: {report.fetched_transactions}")
        for account_result in report.account_results:
            print(
                f"Account {account_result.account_alias} "
                f"({account_result.account_name}) "
                f"server_knowledge={account_result.returned_server_knowledge} "
                f"changed={len(account_result.transactions)}"
            )
            for transaction in account_result.transactions:
                amount = format_milliunits(transaction.amount_milliunits, places=3)
                status: list[str] = []
                if transaction.deleted:
                    status.append("deleted")
                if transaction.transfer_transaction_id is not None:
                    status.append("transfer")
                status_text = f" [{' '.join(status)}]" if status else ""
                memo_text = transaction.memo if transaction.memo is not None else ""
                print(
                    f"- {transaction.date.isoformat()} {account_result.account_alias}: "
                    f"{amount}{status_text}"
                )
                print(f"  memo: {memo_text}")
        return 0
    except YmcaError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="get_account_delta.py",
        description=(
            "Fetch account-by-account YNAB delta transactions using a supplied "
            "last_knowledge_of_server value."
        ),
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
            "Limit the delta fetch to one configured account alias. "
            "Repeat to include multiple accounts."
        ),
    )
    parser.add_argument(
        "--last-server-knowledge",
        type=int,
        required=True,
        help="YNAB last_knowledge_of_server value to fetch deltas from.",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(main())
