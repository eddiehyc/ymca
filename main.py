import os
from pathlib import Path
import getpass
import json
import ynab


def load_ynab_api_key(prompt_save: bool = False) -> str:
    """Resolve the YNAB API key.

    Order of precedence:
      1. YNAB_API_KEY environment variable
      2. .ynab_api_key file (cwd then script directory)
      3. Interactive prompt (hidden input)

    If prompt_save is True the user will be offered to save the prompted key to
    a `.ynab_api_key` file in the current working directory.
    """
    # 1) environment variable
    env_key = os.getenv("YNAB_API_KEY")
    if env_key:
        print("Using YNAB API key from YNAB_API_KEY environment variable.")
        return env_key.strip()

    # 2) check for .ynab_api_key in cwd then next to this script
    candidate_paths = [Path.cwd() / ".ynab_api_key", Path(__file__).parent / ".ynab_api_key"]
    for p in candidate_paths:
        try:
            if p.is_file():
                content = p.read_text(encoding="utf-8").strip()
                if content:
                    print(f"Using YNAB API key from {p}")
                    return content
        except Exception:
            # ignore read errors and try next source
            pass

    # 3) prompt the user (hidden input)
    try:
        key = getpass.getpass("Enter YNAB API key (input hidden): ").strip()
    except Exception:
        # fallback to visible input if getpass fails in this environment
        key = input("Enter YNAB API key: ").strip()

    if not key:
        raise RuntimeError(
            "No YNAB API key provided. Set the YNAB_API_KEY environment variable, create a .ynab_api_key file, or provide the key when prompted."
        )

    if prompt_save:
        try:
            save = input("Save this key to .ynab_api_key in the current directory? [y/N]: ").strip().lower()
            if save == "y":
                out_path = Path.cwd() / ".ynab_api_key"
                out_path.write_text(key + "\n", encoding="utf-8")
                print(f"Saved API key to {out_path}")
        except Exception:
            # non-fatal if saving fails
            pass

    return key


def main():
    api_key = load_ynab_api_key(prompt_save=True)
    ynab_configuration = ynab.Configuration(access_token=api_key)
    print("YNAB configuration created.")
    server_knowledge_path = Path.cwd() / ".ynab_server_knowledge"
    try:
        last_known_server_knowledge = int(server_knowledge_path.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        last_known_server_knowledge = 0

    with ynab.ApiClient(ynab_configuration) as api_client:
        transactions_api = ynab.TransactionsApi(api_client)
        response = transactions_api.get_transactions(
            "last-used", last_knowledge_of_server=last_known_server_knowledge
        )

    data = response.data
    new_transactions = getattr(data, "transactions", []) or []
    for transaction in new_transactions:
        print(json.dumps(_serialize_for_display(transaction), indent=2, sort_keys=True, default=str))

    server_knowledge = getattr(data, "server_knowledge", last_known_server_knowledge)
    server_knowledge_path.write_text(f"{server_knowledge}\n", encoding="utf-8")


def _serialize_for_display(value):
    if hasattr(value, "to_dict"):
        return _serialize_for_display(value.to_dict())
    if hasattr(value, "model_dump"):
        return _serialize_for_display(value.model_dump())
    if isinstance(value, dict):
        return {key: _serialize_for_display(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize_for_display(item) for item in value]
    if hasattr(value, "__dict__"):
        return _serialize_for_display(vars(value))
    return value




if __name__ == "__main__":
    main()
