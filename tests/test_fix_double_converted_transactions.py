from __future__ import annotations

from datetime import date
from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from scripts import fix_double_converted_transactions
from tests.fakes import FakeGateway, FakeGatewayContext
from ymca.models import (
    AccountSnapshot,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    TransactionSnapshot,
)


def test_fix_double_converted_transactions_script_repairs_amount_and_memo(
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
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
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
                            amount_milliunits=78520,
                            memo=(
                                "612.49 HKD (FX rate: 0.12821) · "
                                "[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)"
                            ),
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=1,
                )
            ]
        },
    )

    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = fix_double_converted_transactions.main(
        ["--config", str(config_path), "--apply"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.update_batches) == 1
    assert len(gateway.update_batches[0][1]) == 1
    assert gateway.updates[0].amount_milliunits == 612490
    assert gateway.updates[0].memo == "[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)"
    assert "Prepared fixes: 1" in captured.out
    assert "Writes applied: 1" in captured.out


def test_fix_double_converted_transactions_script_repairs_large_negative_amount_with_rounding_drift(
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
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
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
                            amount_milliunits=-1139105,
                            memo=(
                                "-8,885.05 HKD (FX rate: 0.12821) · "
                                "[FX] -69,303.42 HKD (rate: 0.12821 USD/HKD)"
                            ),
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=1,
                )
            ]
        },
    )

    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = fix_double_converted_transactions.main(
        ["--config", str(config_path), "--apply"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.update_batches) == 1
    assert gateway.updates[0].amount_milliunits == -8885050
    assert gateway.updates[0].memo == "[FX] -69,303.42 HKD (rate: 0.12821 USD/HKD)"
    assert "Prepared fixes: 1" in captured.out
    assert "Writes applied: 1" in captured.out


def test_fix_double_converted_transactions_script_skips_already_correct_amount(
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
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
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
                            amount_milliunits=612490,
                            memo=(
                                "612.49 HKD (FX rate: 0.12821) · "
                                "[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)"
                            ),
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=1,
                )
            ]
        },
    )

    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.fix_double_converted_transactions.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = fix_double_converted_transactions.main(["--config", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert gateway.updates == []
    assert gateway.update_batches == []
    assert "Prepared fixes: 0" in captured.out
