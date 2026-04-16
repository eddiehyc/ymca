from __future__ import annotations

from datetime import date
from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from scripts import get_account_delta
from tests.fakes import FakeGateway, FakeGatewayContext
from ymca.models import (
    AccountSnapshot,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    TransactionSnapshot,
)


def test_get_account_delta_script_fetches_enabled_accounts(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """version: 1
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts:
  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
  cash_hkd:
    name: Cash HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
        encoding="utf-8",
    )

    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Cash HKD", deleted=False),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo="Dinner",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=45,
                )
            ],
            "acct-2": [
                TransactionSnapshot(
                    transactions=(),
                    server_knowledge=50,
                )
            ],
        },
    )

    monkeypatch.setattr("scripts.get_account_delta.load_api_key", lambda **_: "secret")
    monkeypatch.setattr(
        "scripts.get_account_delta.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = get_account_delta.main(
        ["--config", str(config_path), "--last-server-knowledge", "12"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert gateway.list_transactions_by_account_calls == [
        ("plan-1", "acct-1", None, 12),
        ("plan-1", "acct-2", None, 12),
    ]
    assert "Requested last_knowledge_of_server: 12" in captured.out
    assert "Returned server knowledge: 50" in captured.out
    assert "Fetched changed transactions: 1" in captured.out
    assert "Account travel_hkd (Travel HKD) server_knowledge=45 changed=1" in captured.out
    assert "- 2026-04-10 travel_hkd: 12.340" in captured.out
    assert "memo: Dinner" in captured.out
    assert "Account cash_hkd (Cash HKD) server_knowledge=50 changed=0" in captured.out


def test_get_account_delta_script_limits_to_selected_account(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """version: 1
plan:
  alias: personal
  name: Example Plan
  base_currency: USD
accounts:
  travel_hkd:
    name: Travel HKD
    currency: HKD
    enabled: true
  cash_hkd:
    name: Cash HKD
    currency: HKD
    enabled: true
fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
""",
        encoding="utf-8",
    )

    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(
                    RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),
                    RemoteAccount(id="acct-2", name="Cash HKD", deleted=False),
                ),
                server_knowledge=1,
            )
        },
        transaction_details={},
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(),
                    server_knowledge=45,
                )
            ]
        },
    )

    monkeypatch.setattr("scripts.get_account_delta.load_api_key", lambda **_: "secret")
    monkeypatch.setattr(
        "scripts.get_account_delta.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = get_account_delta.main(
        [
            "--config",
            str(config_path),
            "--last-server-knowledge",
            "99",
            "--account",
            "travel_hkd",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert gateway.list_transactions_by_account_calls == [
        ("plan-1", "acct-1", None, 99),
    ]
    assert "Account travel_hkd (Travel HKD) server_knowledge=45 changed=0" in captured.out
    assert "cash_hkd" not in captured.out
