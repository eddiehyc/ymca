import getpass
import json
import os
from pathlib import Path

import ynab


API_KEY_FILENAME = ".ynab_api_key"


def load_ynab_api_key() -> str:
    env_key = os.getenv("YNAB_API_KEY")
    if env_key:
        return env_key.strip()

    candidate_paths = [
        Path.cwd() / API_KEY_FILENAME,
        Path(__file__).resolve().parent / API_KEY_FILENAME,
        Path(__file__).resolve().parents[2] / API_KEY_FILENAME,
    ]

    for path in dict.fromkeys(candidate_paths):
        if path.is_file():
            key = path.read_text(encoding="utf-8").strip()
            if key:
                return key

    try:
        key = getpass.getpass("Enter YNAB API key: ").strip()
    except Exception:
        key = input("Enter YNAB API key: ").strip()

    if not key:
        raise RuntimeError(
            "No YNAB API key found. Set YNAB_API_KEY, create a .ynab_api_key file, or enter it when prompted."
        )

    return key


def create_api_client() -> ynab.ApiClient:
    api_key = load_ynab_api_key()
    configuration = ynab.Configuration(access_token=api_key)
    return ynab.ApiClient(configuration)


def main() -> None:
    with create_api_client() as api_client:
        plans_api = ynab.PlansApi(api_client)
        account_api = ynab.AccountsApi(api_client)
        transactions_api = ynab.TransactionsApi(api_client)


        response = account_api.get_accounts(
            plan_id = "your plan id here"
            )

        # print("\n".join(f"{account.name} {account.id}" for account in response.data.accounts))

        response = transactions_api.get_transactions_by_account(
           plan_id = "your plan id here",
           account_id = "your account id here",
           since_date="2026-04-10"
           )

        print(
            json.dumps(
                [transaction.to_dict() for transaction in response.data.transactions],
                indent=2,
                default=str,
            )
        )

        for transaction in response.data.transactions:
            if not transaction.import_id:
                continue
            print(
                f"{transaction.payee_name}\t"
                f"{transaction.import_payee_name_original}\t"
                f"{transaction.import_payee_name}"
            )
