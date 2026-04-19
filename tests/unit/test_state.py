"""Unit tests for :mod:`ymca.state`.

Covers the YAML round-trip, version validation, error translation, and the
:func:`plan_state_for` / :func:`upsert_plan_state` helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ymca.errors import StateError
from ymca.models import AppState, PlanState
from ymca.state import (
    load_state,
    plan_state_for,
    save_state,
    upsert_plan_state,
)


def test_load_state_returns_empty_state_when_file_missing(tmp_path: Path) -> None:
    state = load_state(tmp_path / "state.yaml")

    assert state.version == 1
    assert dict(state.plans) == {}


def test_load_state_raises_on_unsupported_version(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text("version: 99\nplans: {}\n", encoding="utf-8")

    with pytest.raises(StateError, match="Unsupported state version"):
        load_state(state_path)


def test_load_state_raises_on_invalid_yaml(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text("plans: [unterminated\n", encoding="utf-8")

    with pytest.raises(StateError, match="Failed to parse state YAML"):
        load_state(state_path)


def test_load_state_raises_when_root_is_not_mapping(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text("- just\n- a list\n", encoding="utf-8")

    with pytest.raises(StateError, match="state must be a mapping"):
        load_state(state_path)


def test_load_state_raises_when_plan_alias_is_empty(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        "version: 1\nplans:\n  '':\n    plan_id: p1\n", encoding="utf-8"
    )

    with pytest.raises(StateError, match="plans.<alias>"):
        load_state(state_path)


def test_load_state_raises_when_server_knowledge_is_not_int(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        "version: 1\nplans:\n  personal:\n    server_knowledge: 'abc'\n",
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="server_knowledge"):
        load_state(state_path)


def test_save_state_round_trips_values(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"hkd": "acct-1"},
                server_knowledge=7,
            )
        },
    )

    save_state(state_path, state)
    round_tripped = load_state(state_path)

    assert round_tripped.version == 1
    assert round_tripped.plans["personal"].plan_id == "plan-1"
    assert round_tripped.plans["personal"].account_ids == {"hkd": "acct-1"}
    assert round_tripped.plans["personal"].server_knowledge == 7


def test_plan_state_for_returns_none_when_missing() -> None:
    state = AppState(version=1, plans={})
    assert plan_state_for(state, "nonexistent") is None


def test_upsert_plan_state_replaces_existing_entry() -> None:
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="old", account_ids={"hkd": "a1"}, server_knowledge=1
            )
        },
    )

    updated = upsert_plan_state(
        state,
        alias="personal",
        plan_id="new",
        account_ids={"hkd": "a2"},
        server_knowledge=99,
    )

    assert updated.plans["personal"].plan_id == "new"
    assert updated.plans["personal"].account_ids == {"hkd": "a2"}
    assert updated.plans["personal"].server_knowledge == 99


def test_upsert_plan_state_inserts_new_entry_preserving_others() -> None:
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="p1", account_ids={"hkd": "a1"}, server_knowledge=1
            )
        },
    )

    updated = upsert_plan_state(
        state,
        alias="secondary",
        plan_id="p2",
        account_ids={"gbp": "a2"},
        server_knowledge=None,
    )

    assert updated.plans["personal"].plan_id == "p1"
    assert updated.plans["secondary"].plan_id == "p2"
    assert updated.plans["secondary"].server_knowledge is None


def test_save_state_round_trips_sentinel_ids(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"hkd": "acct-1"},
                server_knowledge=7,
                sentinel_ids={"hkd": "sentinel-hkd"},
            )
        },
    )

    save_state(state_path, state)
    round_tripped = load_state(state_path)

    assert round_tripped.plans["personal"].sentinel_ids == {"hkd": "sentinel-hkd"}


def test_save_state_omits_empty_sentinel_ids_for_backward_compat(tmp_path: Path) -> None:
    """Plans without tracking must serialize the same shape they had pre-feature."""
    state_path = tmp_path / "state.yaml"
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="plan-1",
                account_ids={"hkd": "acct-1"},
                server_knowledge=7,
            )
        },
    )

    save_state(state_path, state)
    text = state_path.read_text(encoding="utf-8")
    assert "sentinel_ids" not in text


def test_load_state_tolerates_missing_sentinel_ids(tmp_path: Path) -> None:
    """Old state files (pre-tracking) must still load cleanly."""
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        """version: 1
plans:
  personal:
    plan_id: plan-1
    account_ids:
      hkd: acct-1
    server_knowledge: 7
""",
        encoding="utf-8",
    )

    loaded = load_state(state_path)

    assert loaded.plans["personal"].sentinel_ids == {}


def test_load_state_raises_when_sentinel_ids_is_not_mapping(tmp_path: Path) -> None:
    state_path = tmp_path / "state.yaml"
    state_path.write_text(
        """version: 1
plans:
  personal:
    plan_id: plan-1
    account_ids: {}
    server_knowledge: 1
    sentinel_ids:
      - just-a-list
""",
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="sentinel_ids"):
        load_state(state_path)


def test_upsert_plan_state_preserves_existing_sentinel_ids_when_unspecified() -> None:
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="p1",
                account_ids={"hkd": "a1"},
                server_knowledge=1,
                sentinel_ids={"hkd": "sent-1"},
            )
        },
    )

    updated = upsert_plan_state(
        state,
        alias="personal",
        plan_id="p1",
        account_ids={"hkd": "a1"},
        server_knowledge=2,
    )

    assert updated.plans["personal"].sentinel_ids == {"hkd": "sent-1"}


def test_upsert_plan_state_replaces_sentinel_ids_when_provided() -> None:
    state = AppState(
        version=1,
        plans={
            "personal": PlanState(
                plan_id="p1",
                account_ids={"hkd": "a1"},
                server_knowledge=1,
                sentinel_ids={"hkd": "old-sent"},
            )
        },
    )

    updated = upsert_plan_state(
        state,
        alias="personal",
        plan_id="p1",
        account_ids={"hkd": "a1"},
        server_knowledge=2,
        sentinel_ids={"hkd": "new-sent", "gbp": "gbp-sent"},
    )

    assert updated.plans["personal"].sentinel_ids == {
        "hkd": "new-sent",
        "gbp": "gbp-sent",
    }
