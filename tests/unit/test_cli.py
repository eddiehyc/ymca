from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from _pytest.capture import CaptureFixture
from pytest import MonkeyPatch

from tests.fakes import FakeGateway, FakeGatewayContext
from ymca.cli import (
    _parse_date_argument,
    _print_conversion_summary,
    _prompt_for_start_date,
    main,
)
from ymca.errors import ApiError
from ymca.models import (
    AccountConfig,
    AccountSnapshot,
    AppState,
    ConversionOutcome,
    FxRule,
    PlanConfig,
    PreparedConversion,
    PreparedUpdate,
    RemoteAccount,
    RemotePlan,
    RemoteTransaction,
    RemoteTransactionDetail,
    ResolvedBindings,
    SkippedTransaction,
    SyncRequest,
    TransactionSnapshot,
    TransactionUpdateRequest,
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


def test_sync_apply_updates_state_file(
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

    exit_code = main(["sync", "--apply", "--bootstrap-since", "2026-04-01"])
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


def test_main_translates_ymca_error_to_exit_code_one(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    def _raise(**_: object) -> str:
        raise ApiError("boom")

    monkeypatch.setattr("ymca.cli.load_api_key", _raise)

    exit_code = main(["discover"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "Error: boom" in captured.err


def test_main_translates_keyboard_interrupt_to_one_thirty(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    def _raise(**_: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("ymca.cli.load_api_key", _raise)

    exit_code = main(["discover"])
    captured = capsys.readouterr()

    assert exit_code == 130
    assert "Interrupted." in captured.err


def test_discover_reports_when_no_plans_are_returned(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    gateway = FakeGateway(plans=(), account_snapshots={}, transaction_details={})
    monkeypatch.setattr("ymca.cli.load_api_key", lambda **_: "secret")
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: FakeGatewayContext(gateway))

    exit_code = main(["discover"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No YNAB plans found." in captured.out


def test_discover_reports_when_plan_has_no_accounts(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    gateway = FakeGateway(
        plans=(RemotePlan(id="plan-1", name="Empty Plan", accounts=()),),
        account_snapshots={},
        transaction_details={},
    )
    monkeypatch.setattr("ymca.cli.load_api_key", lambda **_: "secret")
    monkeypatch.setattr("ymca.cli.YnabClient", lambda api_key: FakeGatewayContext(gateway))

    exit_code = main(["discover"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Plan: Empty Plan" in captured.out
    assert "Accounts: none returned" in captured.out


def test_parse_date_argument_rejects_invalid_iso_date() -> None:
    import argparse

    with pytest.raises(argparse.ArgumentTypeError, match="Invalid date"):
        _parse_date_argument("2026/04/01")


def test_parse_date_argument_accepts_iso_date() -> None:
    assert _parse_date_argument("2026-04-01") == date(2026, 4, 1)


def test_prompt_for_start_date_retries_until_valid_input(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    responses = iter(["not-a-date", "2026-04-01"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(responses))

    result = _prompt_for_start_date()
    captured = capsys.readouterr()

    assert result == date(2026, 4, 1)
    assert "Invalid date" in captured.err


def test_print_conversion_summary_rejects_unexpected_outcome_type() -> None:
    with pytest.raises(RuntimeError, match="Unexpected conversion result"):
        _print_conversion_summary(object())


def _sample_plan() -> PlanConfig:
    from decimal import Decimal

    return PlanConfig(
        alias="personal",
        name="Example Plan",
        base_currency="USD",
        accounts=(
            AccountConfig(alias="travel_hkd", name="Travel HKD", currency="HKD", enabled=True),
        ),
        fx_rates={"HKD": FxRule(rate=Decimal("7.8"), rate_text="7.8", divide_to_base=True)},
    )


def test_print_conversion_summary_renders_sync_and_skipped_details(
    capsys: CaptureFixture[str],
) -> None:
    plan = _sample_plan()
    prepared = PreparedConversion(
        bindings=ResolvedBindings(
            plan=plan,
            plan_id="plan-1",
            account_ids={"travel_hkd": "acct-1"},
        ),
        sync_request=SyncRequest(
            last_knowledge_of_server=117506,
            since_date=None,
            used_bootstrap=False,
        ),
        queried_account_ids=("acct-1",),
        fetched_transactions=2,
        fetched_server_knowledge=117506,
        updates=(
            PreparedUpdate(
                transaction_id="txn-1",
                date=date(2026, 4, 10),
                account_alias="travel_hkd",
                account_name="Travel HKD",
                is_transfer=False,
                source_currency="HKD",
                source_amount_milliunits=12340,
                converted_currency="USD",
                converted_amount_milliunits=1582,
                rate_text="7.8",
                pair_label="HKD/USD",
                old_memo="Dinner",
                new_memo="Dinner | [FX] 12.34 HKD (rate: 7.8 HKD/USD)",
                request=TransactionUpdateRequest(
                    transaction_id="txn-1",
                    amount_milliunits=1582,
                    memo="Dinner | [FX] 12.34 HKD (rate: 7.8 HKD/USD)",
                ),
            ),
        ),
        skipped=(
            SkippedTransaction(
                transaction_id="txn-2",
                date=date(2026, 4, 11),
                account_alias="travel_hkd",
                reason="already-converted",
            ),
            SkippedTransaction(
                transaction_id="txn-3",
                date=date(2026, 4, 12),
                account_alias=None,
                reason="deleted",
            ),
        ),
    )
    outcome = ConversionOutcome(
        prepared=prepared,
        applied=False,
        writes_performed=0,
        saved_server_knowledge=None,
        new_state=AppState(version=1, plans={}),
    )

    _print_conversion_summary(outcome)
    captured = capsys.readouterr()

    assert "Mode: DRY RUN" in captured.out
    assert "Sync: last_knowledge_of_server=117506" in captured.out
    assert "- 2026-04-10 travel_hkd: 12.340 HKD -> 1.582 USD" in captured.out
    assert "- skipped 2026-04-11 travel_hkd: already-converted" in captured.out
    assert "- skipped 2026-04-12 <unknown>: deleted" in captured.out
