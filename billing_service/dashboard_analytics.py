from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from .dashboard_anomaly import BillingAnomalyConfig, assess_account_anomaly


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _coerce_meta(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _coerce_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = _safe_str(value)
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _month_label(period: str) -> str:
    try:
        year_str, month_str = period.split("-", 1)
        return datetime(int(year_str), int(month_str), 1, tzinfo=timezone.utc).strftime("%b %Y")
    except Exception:
        return period


def _money_major(amount_minor: int) -> float:
    return round(amount_minor / 100.0, 2)


def _account_ref(scope: str, account_id: str) -> str:
    cleaned_scope = _safe_str(scope) or "user"
    cleaned_account = _safe_str(account_id)
    return f"{cleaned_scope}/{cleaned_account}" if cleaned_account else cleaned_scope


def infer_service_label(activity: Mapping[str, Any]) -> str:
    meta = _coerce_meta(activity.get("meta"))
    actor = _safe_str(activity.get("actor_app")).lower()
    source = _safe_str(meta.get("source")).lower()
    operation = _safe_str(meta.get("operation") or meta.get("workspace_id") or meta.get("checkout_flow")).lower()
    entry_type = _safe_str(activity.get("entry_type")).lower()

    if entry_type == "transfer_in":
        return "Peer token transfer in"
    if entry_type == "transfer_out":
        return "Peer token transfer out"
    if entry_type == "cashout":
        return "Cash-out settlement"
    if meta.get("payment_provider") or meta.get("payment_id") or entry_type == "topup":
        if source == "portal":
            return "NeuralMimicry portal settlement"
        return "NeuralMimicry settlement"
    if entry_type == "grant":
        return "Admin token grant"
    if entry_type == "refund":
        return "Service refund"
    if "voice" in operation or "stt" in operation:
        return "nmstt voice processing"
    if "aarnn" in operation:
        return "AARNN cognitive workload"
    if any(word in operation for word in ("job", "workspace", "solver", "rag", "assistant")):
        return "Refiner orchestration"
    if actor == "refiner":
        return "Refiner orchestration"
    if actor == "billing" and source == "admin":
        return "Billing admin controls"
    if actor == "customers":
        return "Customers identity operations"
    if source == "api":
        return "Service-side token control"
    if source == "portal":
        return "Customer portal activity"
    return "NeuralMimicry ledger activity"


def _activity_title(entry_type: str, meta: Mapping[str, Any]) -> str:
    payment_provider = _safe_str(meta.get("payment_provider") or meta.get("provider"))
    if entry_type == "topup":
        return "Token top-up" if payment_provider else "Balance top-up"
    if entry_type == "grant":
        return "Token grant"
    if entry_type == "transfer_in":
        return "Token transfer received"
    if entry_type == "transfer_out":
        return "Token transfer sent"
    if entry_type == "refund":
        return "Token refund"
    if entry_type == "cashout":
        return "Cash-out request"
    if entry_type == "debit":
        return "Service debit"
    if entry_type == "reserve":
        return "Capacity reserved"
    if entry_type == "release":
        return "Capacity released"
    if entry_type == "sync":
        return "Balance sync"
    if entry_type == "adjust":
        return "Balance adjustment"
    return entry_type.replace("_", " ").title()


def _activity_status(entry_type: str, shortfall: int, delta_tokens: int) -> str:
    if shortfall > 0:
        return "partial"
    if entry_type in {"topup", "grant", "refund", "transfer_in", "transfer_out"}:
        return "settled"
    if entry_type == "cashout":
        return "queued"
    if entry_type == "debit":
        return "captured" if delta_tokens < 0 else "adjusted"
    if entry_type in {"reserve", "release"}:
        return "reserved"
    if entry_type == "sync":
        return "matched" if delta_tokens == 0 else "adjusted"
    return "posted"


def _normalise_activity_record(record: Mapping[str, Any]) -> dict[str, Any]:
    meta = _coerce_meta(record.get("meta"))
    entry_type = _safe_str(record.get("entry_type")).lower() or "adjust"
    delta_tokens = _safe_int(record.get("delta_tokens") if "delta_tokens" in record else record.get("delta"))
    shortfall = _safe_int(record.get("shortfall_tokens") if "shortfall_tokens" in record else record.get("shortfall"))
    payment_minor = _safe_int(record.get("payment_minor") or meta.get("amount_minor") or meta.get("payment_minor"))
    currency = _safe_str(record.get("currency") or meta.get("currency") or meta.get("settlement_currency") or "GBP") or "GBP"
    provider = _safe_str(record.get("provider") or meta.get("payment_provider") or meta.get("provider") or "")
    payment_method = _safe_str(record.get("payment_method") or meta.get("payment_method") or meta.get("method") or "")
    account_id = _safe_str(record.get("account_id") or meta.get("user_id") or "")
    scope = _safe_str(record.get("scope") or record.get("account_scope") or "user") or "user"
    ts = _safe_str(record.get("ts"))
    service_label = infer_service_label({**record, "meta": meta, "entry_type": entry_type})
    reference = _safe_str(record.get("reference") or record.get("request_id") or meta.get("payment_id") or record.get("tx_id"))
    title = _activity_title(entry_type, meta)
    from_user = _safe_str(record.get("from_user") or meta.get("from_user") or meta.get("granted_by"))
    to_user = _safe_str(record.get("to_user") or meta.get("to_user") or meta.get("recipient") or meta.get("target_user"))
    note = _safe_str(meta.get("note") or meta.get("reason"))
    counterparty = ""
    if entry_type == "transfer_in" and from_user:
        counterparty = f"From {from_user}"
    elif entry_type == "transfer_out" and to_user:
        counterparty = f"To {to_user}"
    elif entry_type == "grant" and from_user:
        counterparty = f"Granted by {from_user}"
    subtitle = " · ".join(
        part
        for part in [
            counterparty,
            payment_method.title() if payment_method else "",
            provider.title() if provider else "",
            service_label,
            note,
        ]
        if part
    )
    return {
        "ts": ts,
        "account_id": account_id,
        "scope": scope,
        "account_ref": _account_ref(scope, account_id),
        "entry_type": entry_type,
        "delta_tokens": delta_tokens,
        "shortfall_tokens": shortfall,
        "payment_minor": payment_minor,
        "currency": currency,
        "provider": provider or None,
        "payment_method": payment_method or None,
        "actor_app": _safe_str(record.get("actor_app") or "billing") or "billing",
        "meta": meta,
        "service_label": service_label,
        "title": title,
        "subtitle": subtitle,
        "status": _activity_status(entry_type, shortfall, delta_tokens),
        "direction": "in" if delta_tokens > 0 else "out" if delta_tokens < 0 else "flat",
        "from_user": from_user or None,
        "to_user": to_user or None,
        "counterparty": counterparty or None,
        "reference": reference or None,
    }


def normalise_ledger_entries(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalised = []
    for entry in entries:
        meta = _coerce_meta(entry.get("meta"))
        normalised.append(
            _normalise_activity_record(
                {
                    "ts": entry.get("ts"),
                    "account_id": entry.get("account_id"),
                    "account_scope": entry.get("account_scope") or entry.get("scope"),
                    "entry_type": entry.get("entry_type") if "entry_type" in entry else entry.get("type"),
                    "delta": entry.get("delta"),
                    "shortfall": entry.get("shortfall"),
                    "actor_app": entry.get("actor_app"),
                    "request_id": entry.get("request_id"),
                    "tx_id": entry.get("tx_id"),
                    "meta": meta,
                }
            )
        )
    normalised.sort(key=lambda item: item.get("ts") or "")
    return normalised


def flatten_chain_blocks(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    for block in blocks:
        transactions = block.get("transactions") if isinstance(block.get("transactions"), list) else []
        for envelope in transactions:
            event = envelope.get("event") if isinstance(envelope.get("event"), dict) else {}
            event_kind = _safe_str(event.get("event")).lower()
            base = {
                "ts": envelope.get("ts") or block.get("ts"),
                "actor_app": envelope.get("actor_app"),
                "request_id": envelope.get("request_id"),
                "tx_id": envelope.get("tx_id"),
            }
            if event_kind == "payment_captured":
                meta = _coerce_meta(event.get("meta"))
                if event.get("provider"):
                    meta.setdefault("payment_provider", event.get("provider"))
                if event.get("payment_id"):
                    meta.setdefault("payment_id", event.get("payment_id"))
                if event.get("checkout_flow"):
                    meta.setdefault("checkout_flow", event.get("checkout_flow"))
                if event.get("currency"):
                    meta.setdefault("currency", event.get("currency"))
                if event.get("amount_minor") not in (None, ""):
                    meta.setdefault("amount_minor", event.get("amount_minor"))
                activities.append(
                    _normalise_activity_record(
                        {
                            **base,
                            "account_id": event.get("user_id"),
                            "account_scope": "user",
                            "entry_type": "topup",
                            "delta_tokens": event.get("tokens"),
                            "payment_minor": event.get("amount_minor"),
                            "currency": event.get("currency"),
                            "provider": event.get("provider"),
                            "meta": meta,
                        }
                    )
                )
            elif event_kind == "token_mutation":
                meta = _coerce_meta(event.get("meta"))
                activities.append(
                    _normalise_activity_record(
                        {
                            **base,
                            "account_id": event.get("account_id"),
                            "account_scope": event.get("account_scope"),
                            "entry_type": event.get("entry_type"),
                            "delta_tokens": event.get("delta"),
                            "meta": meta,
                        }
                    )
                )
    activities.sort(key=lambda item: item.get("ts") or "")
    return activities


def build_daily_points(
    activities: Sequence[Mapping[str, Any]],
    *,
    days: int = 30,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _utcnow()
    start = now.date() - timedelta(days=max(days - 1, 0))
    buckets: dict[str, dict[str, Any]] = {}
    provider_sets: dict[str, set[str]] = {}

    for offset in range(days):
        day_key = (start + timedelta(days=offset)).isoformat()
        buckets[day_key] = {
            "date": day_key,
            "topup_tokens": 0,
            "transfer_in_tokens": 0,
            "debit_tokens": 0,
            "transfer_out_tokens": 0,
            "cashout_tokens": 0,
            "grant_tokens": 0,
            "refund_tokens": 0,
            "inflow_tokens": 0,
            "outflow_tokens": 0,
            "shortfall_tokens": 0,
            "payment_minor": 0,
            "event_count": 0,
            "provider_count": 0,
        }
        provider_sets[day_key] = set()

    for raw in activities:
        activity = _normalise_activity_record(raw)
        dt = _coerce_dt(activity.get("ts"))
        if not dt:
            continue
        day_key = dt.date().isoformat()
        if day_key not in buckets:
            continue
        bucket = buckets[day_key]
        entry_type = _safe_str(activity.get("entry_type")).lower()
        delta_tokens = _safe_int(activity.get("delta_tokens"))
        payment_minor = _safe_int(activity.get("payment_minor"))
        bucket["event_count"] += 1
        bucket["shortfall_tokens"] += _safe_int(activity.get("shortfall_tokens"))
        if payment_minor > 0:
            bucket["payment_minor"] += payment_minor
        if activity.get("provider"):
            provider_sets[day_key].add(_safe_str(activity.get("provider")).lower())
        if entry_type == "topup":
            bucket["topup_tokens"] += max(delta_tokens, 0)
            bucket["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "transfer_in":
            bucket["transfer_in_tokens"] += max(delta_tokens, 0)
            bucket["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "debit":
            bucket["debit_tokens"] += abs(delta_tokens)
            bucket["outflow_tokens"] += abs(delta_tokens)
        elif entry_type == "transfer_out":
            bucket["transfer_out_tokens"] += abs(delta_tokens)
            bucket["outflow_tokens"] += abs(delta_tokens)
        elif entry_type == "cashout":
            bucket["cashout_tokens"] += abs(delta_tokens)
            bucket["outflow_tokens"] += abs(delta_tokens)
        elif entry_type == "grant":
            bucket["grant_tokens"] += max(delta_tokens, 0)
            bucket["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "refund":
            bucket["refund_tokens"] += max(delta_tokens, 0)
            bucket["inflow_tokens"] += max(delta_tokens, 0)

    for day_key, providers in provider_sets.items():
        buckets[day_key]["provider_count"] = len(providers)

    return [buckets[key] for key in sorted(buckets)]


def build_statements(activities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for raw in activities:
        activity = _normalise_activity_record(raw)
        dt = _coerce_dt(activity.get("ts"))
        if not dt:
            continue
        period = f"{dt.year:04d}-{dt.month:02d}"
        group = groups.setdefault(
            period,
            {
                "period": period,
                "label": _month_label(period),
                "topup_tokens": 0,
                "transfer_in_tokens": 0,
                "debit_tokens": 0,
                "transfer_out_tokens": 0,
                "cashout_tokens": 0,
                "grant_tokens": 0,
                "refund_tokens": 0,
                "inflow_tokens": 0,
                "outflow_tokens": 0,
                "payment_minor": 0,
                "currency": _safe_str(activity.get("currency") or "GBP") or "GBP",
                "event_count": 0,
                "providers": Counter(),
                "services": Counter(),
                "latest_ts": activity.get("ts"),
                "status": "settled",
            },
        )
        group["event_count"] += 1
        group["services"][activity.get("service_label") or "Unknown"] += abs(_safe_int(activity.get("delta_tokens"))) or 1
        if activity.get("provider"):
            group["providers"][activity["provider"]] += 1
        delta_tokens = _safe_int(activity.get("delta_tokens"))
        if _safe_int(activity.get("payment_minor")) > 0:
            group["payment_minor"] += _safe_int(activity.get("payment_minor"))
        entry_type = _safe_str(activity.get("entry_type")).lower()
        if entry_type == "topup":
            group["topup_tokens"] += max(delta_tokens, 0)
            group["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "transfer_in":
            group["transfer_in_tokens"] += max(delta_tokens, 0)
            group["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "debit":
            group["debit_tokens"] += abs(delta_tokens)
            group["outflow_tokens"] += abs(delta_tokens)
        elif entry_type == "transfer_out":
            group["transfer_out_tokens"] += abs(delta_tokens)
            group["outflow_tokens"] += abs(delta_tokens)
        elif entry_type == "cashout":
            group["cashout_tokens"] += abs(delta_tokens)
            group["outflow_tokens"] += abs(delta_tokens)
            group["status"] = "review"
        elif entry_type == "grant":
            group["grant_tokens"] += max(delta_tokens, 0)
            group["inflow_tokens"] += max(delta_tokens, 0)
        elif entry_type == "refund":
            group["refund_tokens"] += max(delta_tokens, 0)
            group["inflow_tokens"] += max(delta_tokens, 0)
        if _safe_int(activity.get("shortfall_tokens")) > 0:
            group["status"] = "watch"
        if _safe_str(activity.get("ts")) > _safe_str(group.get("latest_ts")):
            group["latest_ts"] = activity.get("ts")

    statements = []
    for period, group in groups.items():
        providers = sorted(group["providers"].items(), key=lambda item: (-item[1], item[0]))
        services = sorted(group["services"].items(), key=lambda item: (-item[1], item[0]))
        statements.append(
            {
                "period": period,
                "label": group["label"],
                "topup_tokens": group["topup_tokens"],
                "transfer_in_tokens": group["transfer_in_tokens"],
                "debit_tokens": group["debit_tokens"],
                "transfer_out_tokens": group["transfer_out_tokens"],
                "cashout_tokens": group["cashout_tokens"],
                "grant_tokens": group["grant_tokens"],
                "refund_tokens": group["refund_tokens"],
                "inflow_tokens": group["inflow_tokens"],
                "outflow_tokens": group["outflow_tokens"],
                "payment_minor": group["payment_minor"],
                "payment_major": _money_major(group["payment_minor"]),
                "currency": group["currency"],
                "event_count": group["event_count"],
                "provider": providers[0][0] if providers else None,
                "top_service": services[0][0] if services else None,
                "status": group["status"],
                "latest_ts": group["latest_ts"],
            }
        )
    statements.sort(key=lambda item: item["period"], reverse=True)
    return statements


def build_service_breakdown(activities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    overall = 0
    for raw in activities:
        activity = _normalise_activity_record(raw)
        label = _safe_str(activity.get("service_label")) or "Unknown"
        entry = totals.setdefault(
            label,
            {"label": label, "events": 0, "tokens": 0, "payment_minor": 0},
        )
        delta_tokens = abs(_safe_int(activity.get("delta_tokens")))
        entry["events"] += 1
        entry["tokens"] += delta_tokens
        entry["payment_minor"] += _safe_int(activity.get("payment_minor"))
        overall += delta_tokens or 1
    rows = []
    for entry in totals.values():
        share = (entry["tokens"] / overall) if overall else 0.0
        rows.append({**entry, "share": round(share, 4)})
    rows.sort(key=lambda item: (item["tokens"], item["payment_minor"], item["events"]), reverse=True)
    return rows


def build_payment_methods(activities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in activities:
        activity = _normalise_activity_record(raw)
        if _safe_str(activity.get("entry_type")).lower() != "topup":
            continue
        provider = _safe_str(activity.get("provider") or "manual") or "manual"
        method = _safe_str(activity.get("payment_method") or "unspecified") or "unspecified"
        key = f"{provider}:{method}"
        item = grouped.setdefault(
            key,
            {
                "provider": provider,
                "payment_method": method,
                "transactions": 0,
                "payment_minor": 0,
                "tokens": 0,
            },
        )
        item["transactions"] += 1
        item["payment_minor"] += _safe_int(activity.get("payment_minor"))
        item["tokens"] += max(_safe_int(activity.get("delta_tokens")), 0)
    rows = []
    for item in grouped.values():
        rows.append({**item, "payment_major": _money_major(item["payment_minor"])})
    rows.sort(key=lambda item: (item["payment_minor"], item["tokens"], item["transactions"]), reverse=True)
    return rows


def build_recent_transactions(activities: Sequence[Mapping[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    rows = [_normalise_activity_record(activity) for activity in activities]
    rows.sort(key=lambda item: item.get("ts") or "", reverse=True)
    return rows[:limit]


def build_provider_breakdown(activities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in activities:
        activity = _normalise_activity_record(raw)
        provider = _safe_str(activity.get("provider") or "unclassified") or "unclassified"
        item = grouped.setdefault(provider, {"provider": provider, "payments": 0, "payment_minor": 0, "tokens": 0})
        if _safe_str(activity.get("entry_type")).lower() == "topup":
            item["payments"] += 1
            item["payment_minor"] += _safe_int(activity.get("payment_minor"))
            item["tokens"] += max(_safe_int(activity.get("delta_tokens")), 0)
    rows = []
    for item in grouped.values():
        rows.append({**item, "payment_major": _money_major(item["payment_minor"])})
    rows.sort(key=lambda item: (item["payment_minor"], item["tokens"], item["payments"]), reverse=True)
    return rows


def build_forecast(daily_points: Sequence[Mapping[str, Any]], *, now: datetime | None = None) -> dict[str, Any]:
    now = now or _utcnow()
    month_key = now.strftime("%Y-%m")
    month_points = [item for item in daily_points if _safe_str(item.get("date")).startswith(month_key)]
    if not month_points:
        month_points = list(daily_points[-7:])
    year = now.year
    month = now.month
    month_days = calendar.monthrange(year, month)[1]
    elapsed = max(1, min(now.day, month_days))
    debit_tokens = sum(_safe_int(item.get("debit_tokens")) for item in month_points)
    topup_tokens = sum(_safe_int(item.get("topup_tokens")) for item in month_points)
    payment_minor = sum(_safe_int(item.get("payment_minor")) for item in month_points)
    return {
        "projected_debit_tokens": int(round((debit_tokens / elapsed) * month_days)),
        "projected_topup_tokens": int(round((topup_tokens / elapsed) * month_days)),
        "projected_payment_minor": int(round((payment_minor / elapsed) * month_days)),
        "projected_payment_major": _money_major(int(round((payment_minor / elapsed) * month_days))),
        "currency": "GBP",
    }


def build_customer_dashboard(
    snapshot: Mapping[str, Any],
    ledger_entries: Sequence[Mapping[str, Any]],
    *,
    btc_rate: float,
    anomaly_config: BillingAnomalyConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utcnow()
    activities = normalise_ledger_entries(ledger_entries)
    daily_points = build_daily_points(activities, now=now)
    statements = build_statements(activities)
    service_breakdown = build_service_breakdown(activities)
    payment_methods = build_payment_methods(activities)
    transactions = build_recent_transactions(activities, limit=14)
    forecast = build_forecast(daily_points, now=now)
    anomaly = assess_account_anomaly(snapshot, activities, daily_points=daily_points, config=anomaly_config, now=now)

    payment_minor_30d = sum(_safe_int(item.get("payment_minor")) for item in daily_points)
    debit_tokens_30d = sum(_safe_int(item.get("debit_tokens")) for item in daily_points)
    topup_tokens_30d = sum(_safe_int(item.get("topup_tokens")) for item in daily_points)
    transfer_in_tokens_30d = sum(_safe_int(item.get("transfer_in_tokens")) for item in daily_points)
    transfer_out_tokens_30d = sum(_safe_int(item.get("transfer_out_tokens")) for item in daily_points)
    grant_tokens_30d = sum(_safe_int(item.get("grant_tokens")) for item in daily_points)
    refund_tokens_30d = sum(_safe_int(item.get("refund_tokens")) for item in daily_points)
    cashout_tokens_30d = sum(_safe_int(item.get("cashout_tokens")) for item in daily_points)
    inflow_tokens_30d = sum(_safe_int(item.get("inflow_tokens")) for item in daily_points)
    outflow_tokens_30d = sum(_safe_int(item.get("outflow_tokens")) for item in daily_points)

    recommendations = []
    if anomaly["posture"] in {"review", "intervene"}:
        recommendations.append(
            {
                "title": "Review account drift",
                "detail": anomaly["summary"],
                "severity": anomaly["tone"],
            }
        )
    if _safe_str(snapshot.get("status")) == "low":
        recommendations.append(
            {
                "title": "Balance is approaching the low threshold",
                "detail": "Top up before the next Refiner or nmstt-heavy workload burst.",
                "severity": "warn",
            }
        )
    if not payment_methods:
        recommendations.append(
            {
                "title": "Add a primary settlement route",
                "detail": "No recent payment method is attached to this billing profile.",
                "severity": "watch",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "title": "Billing posture is stable",
                "detail": "Settlement and debit activity are currently inside the learned NeuralMimicry envelope.",
                "severity": "ok",
            }
        )

    return {
        "scope": "customer",
        "generated_at": now.isoformat(),
        "summary": {
            "balance_tokens": _safe_int(snapshot.get("balance")),
            "available_tokens": _safe_int(snapshot.get("available")),
            "reserved_tokens": _safe_int(snapshot.get("reserved")),
            "paid_balance_tokens": _safe_int(snapshot.get("paid_balance")),
            "free_balance_tokens": _safe_int(snapshot.get("free_balance")),
            "spent_total_tokens": _safe_int(snapshot.get("spent_total")),
            "cashout_total_tokens": _safe_int(snapshot.get("cashout_total")),
            "free_grant_total_tokens": _safe_int(snapshot.get("free_grant_total")),
            "payment_minor_30d": payment_minor_30d,
            "payment_major_30d": _money_major(payment_minor_30d),
            "debit_tokens_30d": debit_tokens_30d,
            "topup_tokens_30d": topup_tokens_30d,
            "transfer_in_tokens_30d": transfer_in_tokens_30d,
            "transfer_out_tokens_30d": transfer_out_tokens_30d,
            "grant_tokens_30d": grant_tokens_30d,
            "refund_tokens_30d": refund_tokens_30d,
            "cashout_tokens_30d": cashout_tokens_30d,
            "inflow_tokens_30d": inflow_tokens_30d,
            "outflow_tokens_30d": outflow_tokens_30d,
            "forecast": forecast,
            "btc_rate": btc_rate,
            "risk": anomaly["risk"],
            "posture": anomaly["posture"],
            "status": _safe_str(snapshot.get("status") or "ok") or "ok",
            "last_topup_at": snapshot.get("last_topup_at"),
            "updated_at": snapshot.get("updated_at"),
        },
        "daily": daily_points,
        "statements": statements[:6],
        "service_breakdown": service_breakdown[:8],
        "payment_methods": payment_methods[:5],
        "transactions": transactions,
        "anomaly": anomaly,
        "recommendations": recommendations,
    }


def _aggregate_snapshots(snapshots: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    totals = {
        "balance": 0,
        "available": 0,
        "reserved": 0,
        "paid_balance": 0,
        "free_balance": 0,
        "spent_total": 0,
        "cashout_total": 0,
        "free_grant_total": 0,
        "shortfall_total": 0,
        "status": "ok",
    }
    status_priority = {"ok": 0, "low": 1, "watch": 2, "review": 3}
    highest_status = totals["status"]
    for snapshot in snapshots:
        totals["balance"] += _safe_int(snapshot.get("balance"))
        totals["available"] += _safe_int(snapshot.get("available"))
        totals["reserved"] += _safe_int(snapshot.get("reserved"))
        totals["paid_balance"] += _safe_int(snapshot.get("paid_balance"))
        totals["free_balance"] += _safe_int(snapshot.get("free_balance"))
        totals["spent_total"] += _safe_int(snapshot.get("spent_total"))
        totals["cashout_total"] += _safe_int(snapshot.get("cashout_total"))
        totals["free_grant_total"] += _safe_int(snapshot.get("free_grant_total"))
        totals["shortfall_total"] += _safe_int(snapshot.get("shortfall_total"))
        snapshot_status = _safe_str(snapshot.get("status") or "ok") or "ok"
        if status_priority.get(snapshot_status, 0) > status_priority.get(highest_status, 0):
            highest_status = snapshot_status
    totals["status"] = highest_status
    return totals


def _activity_by_account(activities: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in activities:
        normalised = _normalise_activity_record(activity)
        account_ref = _safe_str(normalised.get("account_ref"))
        if account_ref:
            buckets[account_ref].append(normalised)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item.get("ts") or "")
    return buckets


def build_admin_dashboard(
    chain_status: Mapping[str, Any],
    blocks: Sequence[Mapping[str, Any]],
    snapshots_by_account: Mapping[str, Mapping[str, Any]],
    *,
    btc_rate: float,
    anomaly_config: BillingAnomalyConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _utcnow()
    activities = flatten_chain_blocks(blocks)
    daily_points = build_daily_points(activities, now=now)
    statements = build_statements(activities)
    service_breakdown = build_service_breakdown(activities)
    payment_methods = build_payment_methods(activities)
    provider_breakdown = build_provider_breakdown(activities)
    transactions = build_recent_transactions(activities, limit=18)
    forecast = build_forecast(daily_points, now=now)
    by_account = _activity_by_account(activities)
    aggregate_snapshot = _aggregate_snapshots(snapshots_by_account.values())
    portfolio_anomaly = assess_account_anomaly(
        aggregate_snapshot,
        activities,
        daily_points=daily_points,
        config=anomaly_config,
        now=now,
    )

    anomaly_queue = []
    top_accounts = []
    for account_ref, account_activities in by_account.items():
        primary = account_activities[0] if account_activities else {}
        account_scope = _safe_str(primary.get("scope") or "user") or "user"
        account_id = _safe_str(primary.get("account_id"))
        snapshot = snapshots_by_account.get(account_ref) or snapshots_by_account.get(account_id) or {
            "balance": 0,
            "available": 0,
            "reserved": 0,
            "paid_balance": 0,
            "free_balance": 0,
            "spent_total": 0,
            "cashout_total": 0,
            "free_grant_total": 0,
            "shortfall_total": 0,
            "status": "ok",
        }
        account_daily = build_daily_points(account_activities, now=now)
        anomaly = assess_account_anomaly(snapshot, account_activities, daily_points=account_daily, config=anomaly_config, now=now)
        movement_tokens = sum(abs(_safe_int(item.get("delta_tokens"))) for item in account_activities)
        payment_minor = sum(_safe_int(item.get("payment_minor")) for item in account_activities)
        row = {
            "scope": account_scope,
            "account_id": account_id,
            "account_ref": account_ref,
            "movement_tokens": movement_tokens,
            "payment_minor": payment_minor,
            "payment_major": _money_major(payment_minor),
            "risk": anomaly["risk"],
            "posture": anomaly["posture"],
            "status": _safe_str(snapshot.get("status") or "ok") or "ok",
            "balance_tokens": _safe_int(snapshot.get("balance")),
            "available_tokens": _safe_int(snapshot.get("available")),
            "summary": anomaly["summary"],
            "recommended_actions": anomaly["recommended_actions"],
        }
        top_accounts.append(row)
        if anomaly["posture"] in {"review", "intervene"}:
            anomaly_queue.append({**row, "signals": anomaly["signals"][:3], "confidence": anomaly["confidence"]})

    top_accounts.sort(key=lambda item: (item["movement_tokens"], item["payment_minor"]), reverse=True)
    anomaly_queue.sort(key=lambda item: item["risk"], reverse=True)

    topup_tokens_recent = sum(_safe_int(item.get("topup_tokens")) for item in daily_points)
    debit_tokens_recent = sum(_safe_int(item.get("debit_tokens")) for item in daily_points)
    cashout_tokens_recent = sum(_safe_int(item.get("cashout_tokens")) for item in daily_points)
    grant_tokens_recent = sum(_safe_int(item.get("grant_tokens")) for item in daily_points)
    transfer_in_tokens_recent = sum(_safe_int(item.get("transfer_in_tokens")) for item in daily_points)
    transfer_out_tokens_recent = sum(_safe_int(item.get("transfer_out_tokens")) for item in daily_points)
    payment_minor_recent = sum(_safe_int(item.get("payment_minor")) for item in daily_points)

    recommendations = []
    if anomaly_queue:
        highest = anomaly_queue[0]
        recommendations.append(
            {
                "title": f"Investigate {highest['account_ref']}",
                "detail": highest["summary"],
                "severity": "danger" if highest["posture"] == "intervene" else "warn",
            }
        )
    elif portfolio_anomaly["posture"] in {"review", "intervene"}:
        recommendations.append(
            {
                "title": "Review portfolio-wide billing drift",
                "detail": portfolio_anomaly["summary"],
                "severity": portfolio_anomaly["tone"],
            }
        )
    if provider_breakdown:
        leader = provider_breakdown[0]
        provider_share = leader["payment_minor"] / max(payment_minor_recent, 1)
        if provider_share >= 0.75:
            recommendations.append(
                {
                    "title": f"Settlement concentration on {leader['provider']}",
                    "detail": "A single provider dominates the recent settlement mix. Consider resilience checks.",
                    "severity": "watch",
                }
            )
    if not recommendations:
        recommendations.append(
            {
                "title": "Recent billing window is stable",
                "detail": "No review-grade anomalies are active inside the observed chain window.",
                "severity": "ok",
            }
        )

    chain = chain_status.get("chain") if isinstance(chain_status.get("chain"), dict) else chain_status
    return {
        "scope": "admin",
        "generated_at": now.isoformat(),
        "chain": {
            "chain_id": chain.get("chain_id"),
            "height": _safe_int(chain.get("height")),
            "head_hash": chain.get("head_hash"),
            "validator_id": chain.get("validator_id"),
            "auth_mode": chain.get("auth_mode"),
            "account_count": _safe_int(chain.get("account_count")),
        },
        "portfolio": {
            "observed_accounts": len(by_account),
            "recent_topup_tokens": topup_tokens_recent,
            "recent_debit_tokens": debit_tokens_recent,
            "recent_cashout_tokens": cashout_tokens_recent,
            "recent_grant_tokens": grant_tokens_recent,
            "recent_transfer_in_tokens": transfer_in_tokens_recent,
            "recent_transfer_out_tokens": transfer_out_tokens_recent,
            "recent_payment_minor": payment_minor_recent,
            "recent_payment_major": _money_major(payment_minor_recent),
            "anomalies_open": len(anomaly_queue),
            "forecast": forecast,
            "btc_rate": btc_rate,
            "window_blocks": len(blocks),
            "risk": portfolio_anomaly["risk"],
            "posture": portfolio_anomaly["posture"],
        },
        "daily": daily_points,
        "service_breakdown": service_breakdown[:10],
        "payment_methods": payment_methods[:8],
        "provider_breakdown": provider_breakdown[:8],
        "statements": statements[:8],
        "transactions": transactions,
        "anomaly": portfolio_anomaly,
        "top_accounts": top_accounts[:10],
        "anomaly_queue": anomaly_queue[:8],
        "recommendations": recommendations,
    }


def extract_observed_accounts(blocks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    activities = flatten_chain_blocks(blocks)
    buckets: dict[str, dict[str, Any]] = {}
    for activity in activities:
        account_id = _safe_str(activity.get("account_id"))
        scope = _safe_str(activity.get("scope") or "user") or "user"
        if not account_id:
            continue
        account_ref = _account_ref(scope, account_id)
        item = buckets.setdefault(
            account_ref,
            {
                "scope": scope,
                "account_id": account_id,
                "account_ref": account_ref,
                "event_count": 0,
                "movement_tokens": 0,
                "last_ts": activity.get("ts"),
            },
        )
        item["event_count"] += 1
        item["movement_tokens"] += abs(_safe_int(activity.get("delta_tokens")))
        if _safe_str(activity.get("ts")) > _safe_str(item.get("last_ts")):
            item["last_ts"] = activity.get("ts")
    accounts = list(buckets.values())
    accounts.sort(
        key=lambda item: (item["event_count"], item["movement_tokens"], _safe_str(item.get("last_ts"))),
        reverse=True,
    )
    return accounts
