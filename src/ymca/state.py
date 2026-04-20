from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from .errors import StateError
from .models import STATE_VERSION, AppState, PlanState


def load_state(path: Path) -> AppState:
    if not path.exists():
        return AppState(version=STATE_VERSION, plans={})

    raw = _load_yaml_mapping(path)
    version = _parse_int(raw.get("version", STATE_VERSION), "version")
    if version != STATE_VERSION:
        raise StateError(f"Unsupported state version {version}. Expected {STATE_VERSION}.")

    raw_plans = raw.get("plans", {})
    plans_map = _require_mapping(raw_plans, "plans")

    plans: dict[str, PlanState] = {}
    for alias, raw_plan in plans_map.items():
        alias_text = _parse_non_empty_string(alias, "plans.<alias>")
        plan_map = _require_mapping(raw_plan, f"plans.{alias_text}")
        raw_account_ids = plan_map.get("account_ids", {})
        account_ids_map = _require_mapping(raw_account_ids, f"plans.{alias_text}.account_ids")
        account_ids = {
            _parse_non_empty_string(
                account_alias, f"plans.{alias_text}.account_ids.<alias>"
            ): _parse_non_empty_string(account_id, f"plans.{alias_text}.account_ids.<id>")
            for account_alias, account_id in account_ids_map.items()
        }
        plan_id = plan_map.get("plan_id")
        if plan_id is not None:
            plan_id = _parse_non_empty_string(plan_id, f"plans.{alias_text}.plan_id")
        server_knowledge = plan_map.get("server_knowledge")
        if server_knowledge is not None:
            server_knowledge = _parse_int(server_knowledge, f"plans.{alias_text}.server_knowledge")

        raw_sentinel_ids = plan_map.get("sentinel_ids") or {}
        sentinel_ids_map = _require_mapping(raw_sentinel_ids, f"plans.{alias_text}.sentinel_ids")
        sentinel_ids = {
            _parse_non_empty_string(
                account_alias, f"plans.{alias_text}.sentinel_ids.<alias>"
            ): _parse_non_empty_string(
                txn_id, f"plans.{alias_text}.sentinel_ids.<txn_id>"
            )
            for account_alias, txn_id in sentinel_ids_map.items()
        }

        plans[alias_text] = PlanState(
            plan_id=plan_id,
            account_ids=account_ids,
            server_knowledge=server_knowledge,
            sentinel_ids=sentinel_ids,
        )

    return AppState(version=version, plans=plans)


def save_state(path: Path, state: AppState) -> None:
    payload = {
        "version": state.version,
        "plans": {
            alias: _plan_payload(plan_state)
            for alias, plan_state in state.plans.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _plan_payload(plan_state: PlanState) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "plan_id": plan_state.plan_id,
        "account_ids": dict(plan_state.account_ids),
        "server_knowledge": plan_state.server_knowledge,
    }
    # Omit the sentinel map when empty so plans without local-currency tracking
    # keep the same on-disk shape they had before the feature shipped.
    if plan_state.sentinel_ids:
        payload["sentinel_ids"] = dict(plan_state.sentinel_ids)
    return payload


def plan_state_for(state: AppState, alias: str) -> PlanState | None:
    return state.plans.get(alias)


def upsert_plan_state(
    state: AppState,
    *,
    alias: str,
    plan_id: str,
    account_ids: Mapping[str, str],
    server_knowledge: int | None,
    sentinel_ids: Mapping[str, str] | None = None,
) -> AppState:
    plans = dict(state.plans)
    existing = plans.get(alias)
    if sentinel_ids is None:
        merged_sentinel_ids: Mapping[str, str] = (
            dict(existing.sentinel_ids) if existing is not None else {}
        )
    else:
        merged_sentinel_ids = dict(sentinel_ids)
    plans[alias] = PlanState(
        plan_id=plan_id,
        account_ids=dict(account_ids),
        server_knowledge=server_knowledge,
        sentinel_ids=merged_sentinel_ids,
    )
    return AppState(version=STATE_VERSION, plans=plans)


def _load_yaml_mapping(path: Path) -> Mapping[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise StateError(f"Failed to parse state YAML at {path}: {exc}") from exc

    return _require_mapping(loaded, "state")


def _require_mapping(value: Any, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateError(f"{field_name} must be a mapping.")
    return value


def _parse_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _parse_int(value: Any, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise StateError(f"{field_name} must be an integer.")
    return value
