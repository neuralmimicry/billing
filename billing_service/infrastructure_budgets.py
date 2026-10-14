"""Billing-owned ceilings and idempotent holds for temporary infrastructure.

This module records authority and reconciliation only. It never calls a cloud
provider; Continuum remains the only service allowed to mutate provider state.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, Integer, String, Table, Text, UniqueConstraint, insert, select, update
from sqlalchemy.exc import IntegrityError

from .commerce import metadata


budgets = Table(
    "billing_infrastructure_budgets",
    metadata,
    Column("budget_id", String(36), primary_key=True),
    Column("idempotency_key", String(80), nullable=False, unique=True),
    Column("name", String(160), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("ceiling_minor", Integer, nullable=False),
    Column("providers_json", Text, nullable=False),
    Column("starts_at", String(40), nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("status", String(24), nullable=False),
    Column("created_by", String(128), nullable=False),
    Column("approved_by", String(128), nullable=False),
    Column("created_at", String(40), nullable=False),
    UniqueConstraint("budget_id", "idempotency_key"),
)

holds = Table(
    "billing_infrastructure_budget_holds",
    metadata,
    Column("hold_id", String(36), primary_key=True),
    Column("budget_id", String(36), nullable=False, index=True),
    Column("request_id", String(80), nullable=False),
    Column("provider", String(16), nullable=False),
    Column("currency", String(3), nullable=False),
    Column("maximum_cost_minor", Integer, nullable=False),
    Column("expires_at", String(40), nullable=False),
    Column("maintenance_proposal_id", String(128), nullable=False),
    Column("status", String(24), nullable=False),
    Column("actual_cost_minor", Integer),
    Column("operation_state", String(24)),
    Column("provider_job_id", String(160)),
    Column("created_by", String(128), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("reconciled_at", String(40)),
    UniqueConstraint("budget_id", "request_id"),
)

budget_events = Table(
    "billing_infrastructure_budget_events",
    metadata,
    Column("event_id", String(36), primary_key=True),
    Column("budget_id", String(36), nullable=False, index=True),
    Column("hold_id", String(36)),
    Column("event_type", String(40), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("details_json", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
)


class BudgetError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code = code
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BudgetError("timestamp_required", 400)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise BudgetError("invalid_timestamp", 400) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BudgetError("timestamp_timezone_required", 400)
    return parsed.astimezone(timezone.utc)


def _text(value: Any, field: str, limit: int, pattern: str | None = None) -> str:
    if not isinstance(value, str):
        raise BudgetError(f"{field}_required", 400)
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit or (pattern and not re.fullmatch(pattern, cleaned)):
        raise BudgetError(f"invalid_{field}", 400)
    return cleaned


def _amount(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 2_000_000_000:
        raise BudgetError(f"invalid_{field}", 400)
    return value


def _currency(value: Any) -> str:
    currency = _text(value, "currency", 3, r"[A-Za-z]{3}").upper()
    if currency not in {"GBP", "USD", "EUR"}:
        raise BudgetError("unsupported_currency", 400)
    return currency


def _provider_list(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise BudgetError("providers_required", 400)
    providers = sorted({str(item).strip().lower() for item in value})
    if len(providers) != len(value) or not set(providers) <= {"aws", "gcp", "azure"}:
        raise BudgetError("invalid_providers", 400)
    return providers


def _event(conn, budget_id: str, hold_id: str | None, event_type: str, actor: str, details: dict[str, Any]) -> None:
    conn.execute(
        insert(budget_events).values(
            event_id=str(uuid.uuid4()),
            budget_id=budget_id,
            hold_id=hold_id,
            event_type=event_type,
            actor=actor,
            details_json=json.dumps(details, sort_keys=True, separators=(",", ":")),
            created_at=_iso(_now()),
        )
    )


def _budget_json(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["providers"] = json.loads(item.pop("providers_json"))
    item["ceiling"] = {"currency": item["currency"], "minor_units": item.pop("ceiling_minor")}
    return item


def _hold_json(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["maximum_cost"] = {"currency": item["currency"], "minor_units": item.pop("maximum_cost_minor")}
    actual = item.pop("actual_cost_minor")
    item["actual_cost"] = None if actual is None else {"currency": item["currency"], "minor_units": actual}
    return item


def create_budget(engine, payload: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
    key = _text(payload.get("idempotency_key"), "idempotency_key", 80, r"[A-Za-z0-9._:-]+")
    name = _text(payload.get("name"), "name", 160)
    currency = _currency(payload.get("currency"))
    ceiling = _amount(payload.get("ceiling_minor"), "ceiling_minor")
    providers = _provider_list(payload.get("providers"))
    starts = _parse_time(payload.get("starts_at"))
    expires = _parse_time(payload.get("expires_at"))
    now = _now()
    if expires <= starts or expires <= now or starts > now + timedelta(days=366) or expires - starts > timedelta(days=366):
        raise BudgetError("invalid_budget_window", 400)
    fields = {
        "name": name,
        "currency": currency,
        "ceiling_minor": ceiling,
        "providers_json": json.dumps(providers, separators=(",", ":")),
        "starts_at": _iso(starts),
        "expires_at": _iso(expires),
    }
    with engine.begin() as conn:
        prior = conn.execute(select(budgets).where(budgets.c.idempotency_key == key)).mappings().first()
        if prior:
            if any(prior[field] != value for field, value in fields.items()):
                raise BudgetError("budget_idempotency_conflict")
            return _budget_json(prior), True
        budget_id = str(uuid.uuid4())
        values = {
            **fields,
            "budget_id": budget_id,
            "idempotency_key": key,
            "status": "approved",
            "created_by": actor,
            "approved_by": actor,
            "created_at": _iso(now),
        }
        try:
            with conn.begin_nested():
                conn.execute(insert(budgets).values(**values))
        except IntegrityError:
            # A concurrent retry may have won the unique idempotency key.
            prior = conn.execute(select(budgets).where(budgets.c.idempotency_key == key)).mappings().first()
            if prior and all(prior[field] == value for field, value in fields.items()):
                return _budget_json(prior), True
            raise BudgetError("budget_idempotency_conflict")
        _event(conn, budget_id, None, "budget.approved", actor, {"ceiling_minor": ceiling, "currency": currency, "providers": providers})
        return _budget_json(values), False


def _commitment(conn, budget_id: str) -> tuple[int, int]:
    rows = conn.execute(select(holds).where(holds.c.budget_id == budget_id)).mappings().all()
    reserved = 0
    spent = 0
    for row in rows:
        if row["status"] == "reserved":
            reserved += row["maximum_cost_minor"]
        elif row["status"] in {"settled", "overrun"}:
            spent += row["actual_cost_minor"] or 0
    return reserved, spent


def _hold_request_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_id": _text(payload.get("request_id"), "request_id", 80, r"[A-Za-z0-9._:-]+"),
        "provider": _text(payload.get("provider"), "provider", 16, r"[A-Za-z]+" ).lower(),
        "currency": _currency(payload.get("currency")),
        "maximum_cost_minor": _amount(payload.get("maximum_cost_minor"), "maximum_cost_minor"),
        "expires_at": _iso(_parse_time(payload.get("expires_at"))),
        "maintenance_proposal_id": _text(payload.get("maintenance_proposal_id"), "maintenance_proposal_id", 128, r"[A-Za-z0-9._:-]+"),
    }


def create_hold(engine, payload: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
    budget_id = _text(payload.get("budget_id"), "budget_id", 36, r"[0-9a-fA-F-]{36}")
    fields = _hold_request_fields(payload)
    if fields["provider"] not in {"aws", "gcp", "azure"}:
        raise BudgetError("unsupported_provider", 400)
    now = _now()
    expires = _parse_time(fields["expires_at"])
    if expires <= now or expires - now > timedelta(days=31):
        raise BudgetError("invalid_hold_window", 400)
    with engine.begin() as conn:
        budget = conn.execute(select(budgets).where(budgets.c.budget_id == budget_id).with_for_update()).mappings().first()
        if not budget:
            raise BudgetError("budget_not_found", 404)
        prior = conn.execute(
            select(holds).where(holds.c.budget_id == budget_id, holds.c.request_id == fields["request_id"])
        ).mappings().first()
        if prior:
            if any(prior[field] != fields[field] for field in fields if field != "request_id"):
                raise BudgetError("hold_idempotency_conflict")
            return _hold_json(prior), True
        budget_expires = _parse_time(budget["expires_at"])
        if budget["status"] != "approved" or now < _parse_time(budget["starts_at"]) or now >= budget_expires:
            raise BudgetError("budget_not_active")
        if expires > budget_expires:
            raise BudgetError("hold_exceeds_budget_window", 400)
        if fields["currency"] != budget["currency"]:
            raise BudgetError("budget_currency_mismatch", 400)
        if fields["provider"] not in json.loads(budget["providers_json"]):
            raise BudgetError("provider_not_approved", 403)
        reserved, spent = _commitment(conn, budget_id)
        if reserved + spent + fields["maximum_cost_minor"] > budget["ceiling_minor"]:
            raise BudgetError("budget_ceiling_exceeded", 409)
        hold_id = str(uuid.uuid4())
        values = {
            **fields,
            "hold_id": hold_id,
            "budget_id": budget_id,
            "status": "reserved",
            "actual_cost_minor": None,
            "operation_state": None,
            "provider_job_id": None,
            "created_by": actor,
            "created_at": _iso(now),
            "reconciled_at": None,
        }
        try:
            with conn.begin_nested():
                conn.execute(insert(holds).values(**values))
        except IntegrityError:
            prior = conn.execute(
                select(holds).where(holds.c.budget_id == budget_id, holds.c.request_id == fields["request_id"])
            ).mappings().first()
            if prior and all(prior[field] == fields[field] for field in fields if field != "request_id"):
                return _hold_json(prior), True
            raise BudgetError("hold_idempotency_conflict")
        _event(conn, budget_id, hold_id, "budget.hold_reserved", actor, {"request_id": fields["request_id"], "maximum_cost_minor": fields["maximum_cost_minor"], "currency": fields["currency"], "provider": fields["provider"]})
        return _hold_json(values), False


def reconcile_hold(engine, hold_id: str, payload: dict[str, Any], actor: str) -> tuple[dict[str, Any], bool]:
    cleaned_id = _text(hold_id, "hold_id", 36, r"[0-9a-fA-F-]{36}")
    actual = payload.get("actual_cost_minor")
    if isinstance(actual, bool) or not isinstance(actual, int) or actual < 0 or actual > 2_000_000_000:
        raise BudgetError("invalid_actual_cost_minor", 400)
    operation_state = _text(payload.get("operation_state"), "operation_state", 24, r"[a-z_]+")
    if operation_state not in {"not_started", "stopped", "completed", "failed"}:
        raise BudgetError("invalid_operation_state", 400)
    provider_job_id = payload.get("provider_job_id")
    if provider_job_id is not None:
        provider_job_id = _text(provider_job_id, "provider_job_id", 160, r"[A-Za-z0-9._:/-]+")
    if operation_state == "not_started" and (actual != 0 or provider_job_id):
        raise BudgetError("not_started_requires_zero_cost", 400)
    if operation_state != "not_started" and not provider_job_id:
        raise BudgetError("provider_job_id_required", 400)
    now = _iso(_now())
    with engine.begin() as conn:
        before_lock = conn.execute(select(holds).where(holds.c.hold_id == cleaned_id)).mappings().first()
        if not before_lock:
            raise BudgetError("hold_not_found", 404)
        # Re-lock the budget row so a concurrent reservation observes either
        # the original hold or its reconciled actual cost, never neither.
        conn.execute(
            select(budgets.c.budget_id)
            .where(budgets.c.budget_id == before_lock["budget_id"])
            .with_for_update()
        ).first()
        hold = conn.execute(select(holds).where(holds.c.hold_id == cleaned_id).with_for_update()).mappings().first()
        if not hold:
            raise BudgetError("hold_not_found", 404)
        if hold["status"] in {"released", "settled", "overrun"}:
            expected_status = "released" if actual == 0 and operation_state in {"not_started", "stopped"} else ("overrun" if actual > hold["maximum_cost_minor"] else "settled")
            if (hold["actual_cost_minor"], hold["operation_state"], hold["provider_job_id"], hold["status"]) == (actual, operation_state, provider_job_id, expected_status):
                return _hold_json(hold), True
            raise BudgetError("hold_reconciliation_conflict")
        if hold["status"] != "reserved":
            raise BudgetError("hold_not_reconcilable")
        status = "released" if actual == 0 and operation_state in {"not_started", "stopped"} else ("overrun" if actual > hold["maximum_cost_minor"] else "settled")
        conn.execute(
            update(holds)
            .where(holds.c.hold_id == cleaned_id, holds.c.status == "reserved")
            .values(status=status, actual_cost_minor=actual, operation_state=operation_state, provider_job_id=provider_job_id, reconciled_at=now)
        )
        details = {"actual_cost_minor": actual, "currency": hold["currency"], "operation_state": operation_state, "provider_job_id": provider_job_id, "status": status}
        _event(conn, hold["budget_id"], cleaned_id, "budget.hold_reconciled", actor, details)
        updated = conn.execute(select(holds).where(holds.c.hold_id == cleaned_id)).mappings().one()
        return _hold_json(updated), False


def get_hold(engine, hold_id: str) -> dict[str, Any] | None:
    cleaned_id = _text(hold_id, "hold_id", 36, r"[0-9a-fA-F-]{36}")
    with engine.connect() as conn:
        row = conn.execute(select(holds).where(holds.c.hold_id == cleaned_id)).mappings().first()
    return _hold_json(row) if row else None


def list_budgets(engine, limit: int = 100) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(select(budgets).order_by(budgets.c.created_at.desc()).limit(max(1, min(limit, 200)))).mappings().all()
    return [_budget_json(row) for row in rows]


def disable_budget(engine, budget_id: str, reason: str, actor: str) -> tuple[dict[str, Any], bool]:
    cleaned_id = _text(budget_id, "budget_id", 36, r"[0-9a-fA-F-]{36}")
    cleaned_reason = _text(reason, "reason", 500)
    with engine.begin() as conn:
        row = conn.execute(select(budgets).where(budgets.c.budget_id == cleaned_id).with_for_update()).mappings().first()
        if not row:
            raise BudgetError("budget_not_found", 404)
        if row["status"] == "disabled":
            return _budget_json(row), True
        if row["status"] != "approved":
            raise BudgetError("budget_not_active")
        conn.execute(update(budgets).where(budgets.c.budget_id == cleaned_id).values(status="disabled"))
        _event(conn, cleaned_id, None, "budget.disabled", actor, {"reason": cleaned_reason})
        updated = conn.execute(select(budgets).where(budgets.c.budget_id == cleaned_id)).mappings().one()
        return _budget_json(updated), False


def list_budget_events(engine, budget_id: str, limit: int = 200) -> list[dict[str, Any]]:
    cleaned_id = _text(budget_id, "budget_id", 36, r"[0-9a-fA-F-]{36}")
    with engine.connect() as conn:
        rows = conn.execute(
            select(budget_events).where(budget_events.c.budget_id == cleaned_id).order_by(budget_events.c.created_at).limit(max(1, min(limit, 500)))
        ).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["details"] = json.loads(item.pop("details_json"))
        result.append(item)
    return result
