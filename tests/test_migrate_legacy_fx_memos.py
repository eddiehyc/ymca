from __future__ import annotations

from datetime import date
from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from scripts import migrate_legacy_fx_memos
from tests.fakes import FakeGateway, FakeGatewayContext
from ymca.models import (
    AccountSnapshot,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    TransactionSnapshot,
)


def test_migrate_legacy_fx_memos_script_updates_old_marker(
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
                            amount_milliunits=-45586690,
                            memo="Dinner | -45,586.69 HKD (FX rate: 0.12821)",
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
        "scripts.migrate_legacy_fx_memos.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.migrate_legacy_fx_memos.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = migrate_legacy_fx_memos.main(["--config", str(config_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.updates) == 1
    assert len(gateway.update_batches) == 1
    assert len(gateway.update_batches[0][1]) == 1
    assert gateway.updates[0].amount_milliunits is None
    assert gateway.updates[0].memo == "Dinner | [FX] -45,586.69 HKD (rate: 0.12821 USD/HKD)"
    assert "Prepared memo migrations: 1" in captured.out
    assert "Writes applied: 1" in captured.out


def test_migrate_legacy_fx_memos_script_moves_prepended_marker_to_end(
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
                            amount_milliunits=-78000,
                            memo="-/+78 HKD (FX rate: 0.12821) · FPS",
                            account_id="acct-1",
                            transfer_account_id="acct-2",
                            transfer_transaction_id="txn-2",
                            deleted=False,
                        ),
                    ),
                    server_knowledge=1,
                )
            ]
        },
    )

    monkeypatch.setattr(
        "scripts.migrate_legacy_fx_memos.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.migrate_legacy_fx_memos.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = migrate_legacy_fx_memos.main(["--config", str(config_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.updates) == 1
    assert len(gateway.update_batches) == 1
    assert gateway.updates[0].amount_milliunits is None
    assert gateway.updates[0].memo == "FPS | [FX] -/+78 HKD (rate: 0.12821 USD/HKD)"
    assert "Prepared memo migrations: 1" in captured.out
    assert "Writes applied: 1" in captured.out


def test_migrate_legacy_fx_memos_script_normalizes_ungrouped_amount(
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
                            amount_milliunits=-7500000,
                            memo="-7500 HKD (FX rate: 0.12821)",
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
        "scripts.migrate_legacy_fx_memos.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.migrate_legacy_fx_memos.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = migrate_legacy_fx_memos.main(["--config", str(config_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.updates) == 1
    assert len(gateway.update_batches) == 1
    assert gateway.updates[0].amount_milliunits is None
    assert gateway.updates[0].memo == "[FX] -7,500 HKD (rate: 0.12821 USD/HKD)"
    assert "Prepared memo migrations: 1" in captured.out
    assert "Writes applied: 1" in captured.out


def test_migrate_legacy_fx_memos_script_batches_multiple_updates_for_one_account(
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
                            amount_milliunits=-100000,
                            memo="-100 HKD (FX rate: 0.12821)",
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                        RemoteTransaction(
                            id="txn-2",
                            date=date(2026, 4, 11),
                            amount_milliunits=-200000,
                            memo="-200 HKD (FX rate: 0.12821)",
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
        "scripts.migrate_legacy_fx_memos.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr(
        "scripts.migrate_legacy_fx_memos.YnabClient",
        lambda api_key: FakeGatewayContext(gateway),
    )

    exit_code = migrate_legacy_fx_memos.main(["--config", str(config_path), "--apply"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert len(gateway.updates) == 2
    assert len(gateway.update_batches) == 1
    assert tuple(request.transaction_id for request in gateway.update_batches[0][1]) == (
        "txn-1",
        "txn-2",
    )
    assert "Prepared memo migrations: 2" in captured.out
    assert "Writes applied: 2" in captured.out
