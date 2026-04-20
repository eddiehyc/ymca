from __future__ import annotations

from datetime import date
from pathlib import Path

from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from deprecated.one_off_scripts import (
    fix_double_converted_transactions,
    get_account_delta,
    migrate_legacy_fx_memos,
)
from tests.workflows.helpers import (
    InMemoryGateway,
    InMemoryGatewayContext,
    SimulatedAccount,
    SimulatedTransaction,
)
from ymca.cli import main
from ymca.memo import SENTINEL_PAYEE_NAME
from ymca.models import TransactionUpdateRequest
from ymca.state import load_state


def _write_config(
    path: Path,
    *,
    track_local_balance: bool = False,
    include_gbp: bool = False,
) -> None:
    gbp_block = (
        """
  travel_gbp:
    name: Travel GBP
    currency: GBP
    enabled: true
"""
        if include_gbp
        else ""
    )
    tracking_line = "    track_local_balance: true\n" if track_local_balance else ""
    config_text = f"""version: 1
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
{tracking_line}{gbp_block}fx_rates:
  HKD:
    rate: "7.8"
    divide_to_base: true
"""
    if include_gbp:
        config_text += """  GBP:
    rate: "1.35"
    divide_to_base: false
"""
    path.write_text(config_text, encoding="utf-8")


def _patch_cli_gateway(
    monkeypatch: MonkeyPatch,
    *,
    gateway: InMemoryGateway,
    config_path: Path,
    state_path: Path,
) -> None:
    monkeypatch.setenv("YMCA_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("YMCA_STATE_PATH", str(state_path))
    monkeypatch.setattr("ymca.cli.load_api_key", lambda **_: "secret")
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: InMemoryGatewayContext(gateway))


def _patch_script_gateway(
    monkeypatch: MonkeyPatch,
    module_name: str,
    gateway: InMemoryGateway,
) -> None:
    monkeypatch.setattr(f"{module_name}.load_api_key", lambda **_: "secret")
    monkeypatch.setattr(
        f"{module_name}.YnabClient",
        lambda api_key: InMemoryGatewayContext(gateway),
    )


def test_sync_apply_then_quiet_delta_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    state_path = tmp_path / "state.yaml"
    _write_config(config_path)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(SimulatedAccount(id="acct-hkd", name="Travel HKD"),),
        transactions=(
            SimulatedTransaction(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="Dinner",
                account_id="acct-hkd",
                payee_name="Dinner",
            ),
        ),
    )
    _patch_cli_gateway(
        monkeypatch,
        gateway=gateway,
        config_path=config_path,
        state_path=state_path,
    )

    first_exit = main(["sync", "--apply", "--bootstrap-since", "2026-04-01"])
    first_output = capsys.readouterr()

    assert first_exit == 0
    assert "Writes applied: 1" in first_output.out
    converted = gateway.detail("txn-1")
    assert converted.amount_milliunits == -1582
    assert converted.memo == "Dinner | [FX] -12.34 HKD (rate: 7.8 HKD/USD)"

    saved_state = load_state(state_path)
    saved_knowledge = saved_state.plans["personal"].server_knowledge
    assert saved_knowledge is not None

    writes_before_second_run = len(gateway.updates)
    second_exit = main(["sync", "--apply"])
    second_output = capsys.readouterr()

    assert second_exit == 0
    assert "Fetched transactions: 0" in second_output.out
    assert "Prepared updates: 0" in second_output.out
    assert len(gateway.updates) == writes_before_second_run
    assert load_state(state_path).plans["personal"].server_knowledge == saved_knowledge


def test_sync_bootstrap_and_account_filter_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    state_path = tmp_path / "state.yaml"
    _write_config(config_path, include_gbp=True)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(
            SimulatedAccount(id="acct-hkd", name="Travel HKD"),
            SimulatedAccount(id="acct-gbp", name="Travel GBP"),
        ),
        transactions=(
            SimulatedTransaction(
                id="txn-hkd",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="HKD spend",
                account_id="acct-hkd",
                payee_name="HKD spend",
            ),
            SimulatedTransaction(
                id="txn-gbp",
                date=date(2026, 4, 10),
                amount_milliunits=-1000,
                memo="GBP spend",
                account_id="acct-gbp",
                payee_name="GBP spend",
            ),
        ),
    )
    _patch_cli_gateway(
        monkeypatch,
        gateway=gateway,
        config_path=config_path,
        state_path=state_path,
    )

    exit_code = main(
        ["sync", "--apply", "--bootstrap-since", "2026-04-01", "--account", "travel_hkd"]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Writes applied: 1" in output.out
    assert gateway.detail("txn-hkd").amount_milliunits == -1582
    assert gateway.detail("txn-gbp").amount_milliunits == -1000
    assert gateway.detail("txn-gbp").memo == "GBP spend"


def test_local_currency_tracking_lifecycle_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    state_path = tmp_path / "state.yaml"
    _write_config(config_path, track_local_balance=True)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(SimulatedAccount(id="acct-hkd", name="Travel HKD"),),
        transactions=(
            SimulatedTransaction(
                id="txn-1",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="Tracked spend",
                account_id="acct-hkd",
                payee_name="Tracked spend",
                cleared="cleared",
            ),
        ),
    )
    _patch_cli_gateway(
        monkeypatch,
        gateway=gateway,
        config_path=config_path,
        state_path=state_path,
    )

    first_exit = main(["sync", "--apply", "--bootstrap-since", "2026-04-01"])
    first_output = capsys.readouterr()

    assert first_exit == 0
    assert "Sentinel writes: 1 (1 created)" in first_output.out
    tracked = gateway.detail("txn-1")
    assert "[FX+] -12.34 HKD (rate: 7.8 HKD/USD)" in (tracked.memo or "")
    sentinel = gateway.find_active_transaction_by_payee(
        SENTINEL_PAYEE_NAME, account_id="acct-hkd"
    )
    assert "[YMCA-BAL] HKD -12.34" in (sentinel.memo or "")

    saved_state = load_state(state_path)
    sentinel_id = saved_state.plans["personal"].sentinel_ids["travel_hkd"]
    gateway.delete_transaction("plan-1", "txn-1")

    second_exit = main(["sync", "--apply"])
    second_output = capsys.readouterr()

    assert second_exit == 0
    assert "Sentinel writes: 1" in second_output.out
    updated_sentinel = gateway.detail(sentinel_id)
    assert "[YMCA-BAL] HKD 0.00" in (updated_sentinel.memo or "")

    gateway.update_transaction(
        "plan-1",
        TransactionUpdateRequest(
            transaction_id=sentinel_id,
            amount_milliunits=None,
            memo="[YMCA-BAL] HKD 9,999.99",
            flag_color=None,
        ),
    )
    rebuild_exit = main(["sync", "--rebuild-balance", "--apply", "--account", "travel_hkd"])
    rebuild_output = capsys.readouterr()

    assert rebuild_exit == 0
    assert "Balance mode: REBUILD (full scan)" in rebuild_output.out
    rebuilt_sentinel = gateway.detail(sentinel_id)
    assert "[YMCA-BAL] HKD 0.00" in (rebuilt_sentinel.memo or "")

    writes_before_quiet_run = len(gateway.updates)
    quiet_exit = main(["sync", "--apply"])
    quiet_output = capsys.readouterr()

    assert quiet_exit == 0
    assert "Sentinel writes:" not in quiet_output.out
    assert len(gateway.updates) == writes_before_quiet_run


def test_migrate_legacy_memo_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(SimulatedAccount(id="acct-hkd", name="Travel HKD"),),
        transactions=(
            SimulatedTransaction(
                id="txn-legacy",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="-12.34 HKD (FX rate: 7.8) · FPS",
                account_id="acct-hkd",
                payee_name="Legacy",
            ),
        ),
    )
    _patch_script_gateway(
        monkeypatch,
        "deprecated.one_off_scripts.migrate_legacy_fx_memos",
        gateway,
    )

    exit_code = migrate_legacy_fx_memos.main(["--config", str(config_path), "--apply"])
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Prepared memo migrations: 1" in output.out
    assert gateway.detail("txn-legacy").memo == "FPS | [FX] -12.34 HKD (rate: 7.8 USD/HKD)"


def test_fix_double_converted_transaction_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(SimulatedAccount(id="acct-hkd", name="Travel HKD"),),
        transactions=(
            SimulatedTransaction(
                id="txn-double",
                date=date(2026, 4, 10),
                amount_milliunits=78520,
                memo=(
                    "612.49 HKD (FX rate: 0.12821) · "
                    "[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)"
                ),
                account_id="acct-hkd",
                payee_name="Double",
            ),
        ),
    )
    _patch_script_gateway(
        monkeypatch,
        "deprecated.one_off_scripts.fix_double_converted_transactions",
        gateway,
    )

    exit_code = fix_double_converted_transactions.main(
        ["--config", str(config_path), "--apply"]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Prepared fixes: 1" in output.out
    repaired = gateway.detail("txn-double")
    assert repaired.amount_milliunits == 612490
    assert repaired.memo == "[FX] 4,777.44 HKD (rate: 0.12821 USD/HKD)"


def test_get_account_delta_workflow(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    gateway = InMemoryGateway(
        plan_id="plan-1",
        plan_name="Example Plan",
        accounts=(SimulatedAccount(id="acct-hkd", name="Travel HKD"),),
        transactions=(
            SimulatedTransaction(
                id="txn-old",
                date=date(2026, 4, 8),
                amount_milliunits=-1000,
                memo="Old",
                account_id="acct-hkd",
                payee_name="Old",
                modified_knowledge=2,
            ),
            SimulatedTransaction(
                id="txn-new",
                date=date(2026, 4, 10),
                amount_milliunits=-12340,
                memo="New",
                account_id="acct-hkd",
                payee_name="New",
                modified_knowledge=4,
            ),
        ),
    )
    _patch_script_gateway(
        monkeypatch,
        "deprecated.one_off_scripts.get_account_delta",
        gateway,
    )

    exit_code = get_account_delta.main(
        ["--config", str(config_path), "--last-server-knowledge", "3"]
    )
    output = capsys.readouterr()

    assert exit_code == 0
    assert "Fetched changed transactions: 1" in output.out
    assert "txn-new" not in output.out
    assert "- 2026-04-10 travel_hkd: -12.340" in output.out
