from __future__ import annotations

from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from tests.fakes import FakeGateway, FakeGatewayContext
from ymca.cli import main
from ymca.models import (
    AccountSnapshot,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    TransactionSnapshot,
)
from ymca.state import load_state


def test_config_init_writes_template(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"

    exit_code = main(["config", "init", "--path", str(config_path)])

    assert exit_code == 0
    assert config_path.read_text(encoding="utf-8").startswith("version: 1")


def test_config_check_reports_success(
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
    )

    monkeypatch.setattr(
        "ymca.cli.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: FakeGatewayContext(gateway))

    exit_code = main(["config", "check", "--path", str(config_path)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Config schema: OK" in captured.out
    assert "YNAB auth: OK" in captured.out
    assert "Account travel_hkd: OK" in captured.out


def test_convert_apply_updates_state_file(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    state_path = tmp_path / "state.yaml"
    config_path.write_text(
        """version: 1
secrets:
  api_key_file: ./ynab_api_key
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

    from datetime import date

    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Example Plan"),),
        account_snapshots={
            "plan-1": AccountSnapshot(
                accounts=(RemoteAccount(id="acct-1", name="Travel HKD", deleted=False),),
                server_knowledge=1,
            )
        },
        transaction_details={
            "txn-1": RemoteTransactionDetail(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=12340,
                memo=None,
                account_id="acct-1",
                transfer_account_id=None,
                transfer_transaction_id=None,
                deleted=False,
                subtransaction_count=0,
            )
        },
        transaction_snapshots_by_account={
            "acct-1": [
                TransactionSnapshot(
                    transactions=(
                        RemoteTransaction(
                            id="txn-1",
                            date=date(2026, 4, 10),
                            amount_milliunits=12340,
                            memo=None,
                            account_id="acct-1",
                            transfer_account_id=None,
                            transfer_transaction_id=None,
                            deleted=False,
                        ),
                    ),
                    server_knowledge=44,
                ),
                TransactionSnapshot(transactions=(), server_knowledge=55),
            ]
        },
    )

    monkeypatch.setenv("YMCA_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YMCA_STATE_PATH", str(state_path))
    monkeypatch.setattr(
        "ymca.cli.load_api_key",
        lambda **_: "secret",
    )
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: FakeGatewayContext(gateway))

    exit_code = main(["convert", "--apply", "--bootstrap-since", "2026-04-01"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Mode: APPLY" in captured.out
    assert "Writes applied: 1" in captured.out
    saved_state = load_state(state_path)
    assert saved_state.plans["personal"].server_knowledge == 55


def test_discover_hides_closed_accounts(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    gateway = FakeGateway(
        plans=(
            RemotePlan(
                id="plan-1",
                name="Example Plan",
                accounts=(
                    RemoteAccount(id="acct-1", name="Open HKD", deleted=False, closed=False),
                    RemoteAccount(id="acct-2", name="Closed HKD", deleted=False, closed=True),
                    RemoteAccount(id="acct-3", name="Deleted HKD", deleted=True, closed=False),
                ),
            ),
        ),
        account_snapshots={},
        transaction_details={},
    )

    monkeypatch.setattr("ymca.cli.load_api_key", lambda **_: "secret")
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: FakeGatewayContext(gateway))

    exit_code = main(["discover"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Plan: Example Plan" in captured.out
    assert "Open HKD" in captured.out
    assert "Closed HKD" not in captured.out
    assert "Deleted HKD" not in captured.out
