from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from statistics import mean
from typing import Any, Mapping, Sequence


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _sigmoid(value: float) -> float:
    if value >= 0:
        exp = math.exp(-value)
        return 1.0 / (1.0 + exp)
    exp = math.exp(value)
    return exp / (1.0 + exp)


def _triangle(value: float, left: float, center: float, right: float) -> float:
    if value <= left or value >= right:
        return 0.0
    if value == center:
        return 1.0
    if value < center:
        return (value - left) / max(center - left, 1e-9)
    return (right - value) / max(right - center, 1e-9)


def _left_shoulder(value: float, left: float, right: float) -> float:
    if value <= left:
        return 1.0
    if value >= right:
        return 0.0
    return (right - value) / max(right - left, 1e-9)


def _right_shoulder(value: float, left: float, right: float) -> float:
    if value <= left:
        return 0.0
    if value >= right:
        return 1.0
    return (value - left) / max(right - left, 1e-9)


def _coerce_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).strip()
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


def _mean(values: Sequence[float]) -> float:
    return mean(values) if values else 0.0


def _stddev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = _mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return variance ** 0.5


@dataclass(slots=True)
class BillingFuzzyConfig:
    enabled: bool = True
    order: int = 3
    uncertainty: float = 0.55
    edge_bias: float = 0.70
    aarnn_weight: float = 0.22
    security_weight: float = 0.28


@dataclass(slots=True)
class BillingNeuroConfig:
    recurrence: float = 0.42
    drift_weight: float = 0.36
    activation_weight: float = 0.34
    novelty_weight: float = 0.30


@dataclass(slots=True)
class BillingAnomalyConfig:
    fuzzy: BillingFuzzyConfig = field(default_factory=BillingFuzzyConfig)
    neuro: BillingNeuroConfig = field(default_factory=BillingNeuroConfig)
    min_risk: float = 0.62
    min_confidence: float = 0.30


class NeuromimicPulseModel:
    """A compact recurrent scorer inspired by NeuralMimicry's AARNN runtime signals.

    The model is intentionally lightweight and deterministic so Billing can reason about
    behavioural drift without introducing a heavyweight external inference dependency.
    """

    def __init__(self, config: BillingNeuroConfig | None = None) -> None:
        self.config = config or BillingNeuroConfig()

    def score(self, sequence_vectors: Sequence[Sequence[float]]) -> dict[str, Any]:
        state = [0.0, 0.0, 0.0]
        novelty_values: list[float] = []
        activation_values: list[float] = []

        for vector in sequence_vectors:
            spend = _safe_float(vector[0])
            settlement = _safe_float(vector[1])
            cashout = _safe_float(vector[2])
            shortfall = _safe_float(vector[3])
            velocity = _safe_float(vector[4])
            churn = _safe_float(vector[5])

            drive = math.tanh(
                1.35 * spend
                + 1.10 * shortfall
                + 0.82 * velocity
                + 0.74 * cashout
                + 0.56 * churn
                + 0.42 * settlement
            )
            gate = _sigmoid(
                1.10 * spend
                + 0.95 * shortfall
                + 0.84 * velocity
                + 0.52 * churn
                - 0.25 * settlement
                + 0.45 * state[0]
                - 0.20 * state[2]
            )
            reserve = math.tanh(
                1.00 * settlement
                + 0.82 * cashout
                + 0.60 * churn
                + 0.35 * state[1]
            )
            coherence = math.tanh(
                0.72 * (1.0 - abs(spend - settlement))
                - 0.65 * shortfall
                - 0.38 * churn
                + 0.24 * state[2]
            )

            next_state = [
                ((1.0 - self.config.recurrence) * state[0]) + (self.config.recurrence * drive * gate),
                ((1.0 - self.config.recurrence) * state[1]) + (self.config.recurrence * reserve),
                ((1.0 - self.config.recurrence) * state[2]) + (self.config.recurrence * coherence),
            ]
            novelty = _clamp(
                abs(next_state[0] - state[0]) * 0.50
                + abs(next_state[1] - state[1]) * 0.30
                + abs(next_state[2] - state[2]) * 0.20
            )
            activation = _clamp(
                abs(next_state[0]) * 0.46
                + abs(next_state[1]) * 0.22
                + abs(next_state[2]) * 0.12
                + novelty * 0.20
            )
            state = next_state
            novelty_values.append(novelty)
            activation_values.append(activation)

        drift_score = _clamp(_mean(novelty_values) + (novelty_values[-1] if novelty_values else 0.0) * 0.35)
        activation_score = _clamp(_mean(activation_values) + abs(state[0]) * 0.12)
        risk = _clamp(
            activation_score * self.config.activation_weight
            + drift_score * self.config.drift_weight
            + _clamp(abs(state[1]) * 0.60 + (1.0 - (state[2] + 1.0) / 2.0) * 0.40) * self.config.novelty_weight
        )

        if risk >= 0.78 or drift_score >= 0.74:
            actions = [
                "Trigger operator review",
                "Harden cash-out verification",
                "Compare activity with Customers identity history",
            ]
        elif risk >= 0.56:
            actions = [
                "Continue observation",
                "Elevate settlement telemetry sampling",
                "Flag next payment for step-up checks",
            ]
        else:
            actions = ["Continue observation"]

        return {
            "risk": risk,
            "drift_score": drift_score,
            "activation": activation_score,
            "state_vector": [round(component, 4) for component in state],
            "actions": actions,
        }


def _build_daily_metrics(entries: Sequence[Mapping[str, Any]], daily_points: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    points = list(daily_points)
    recent_points = points[-7:] if points else []
    historical_points = points[:-7] if len(points) > 7 else points

    spend_series = [_safe_float(item.get("debit_tokens")) for item in historical_points]
    payment_series = [_safe_float(item.get("payment_minor")) for item in historical_points]
    velocity_series = [_safe_float(item.get("event_count")) for item in historical_points]

    recent_spend = sum(_safe_float(item.get("debit_tokens")) for item in recent_points)
    recent_payment_minor = sum(_safe_float(item.get("payment_minor")) for item in recent_points)
    recent_cashout = sum(_safe_float(item.get("cashout_tokens")) for item in recent_points)
    recent_shortfall = sum(_safe_float(item.get("shortfall_tokens")) for item in recent_points)
    recent_velocity = _mean([_safe_float(item.get("event_count")) for item in recent_points])
    recent_churn = _mean([_safe_float(item.get("provider_count")) for item in recent_points])

    spend_mean = _mean(spend_series)
    payment_mean = _mean(payment_series)
    velocity_mean = _mean(velocity_series)
    spend_std = max(_stddev(spend_series), 1.0)
    payment_std = max(_stddev(payment_series), 1.0)
    velocity_std = max(_stddev(velocity_series), 0.5)

    spend_z = abs((recent_spend / max(len(recent_points), 1) - spend_mean) / spend_std)
    payment_z = abs((recent_payment_minor / max(len(recent_points), 1) - payment_mean) / payment_std)
    velocity_z = abs((recent_velocity - velocity_mean) / velocity_std)

    dt_values = [dt for dt in (_coerce_dt(item.get("ts")) for item in entries) if dt is not None]
    dt_values.sort()
    dormant_gap_days = 0.0
    if len(dt_values) >= 2:
        dormant_gap_days = max(0.0, (dt_values[-1] - dt_values[-2]).total_seconds() / 86400.0)

    payment_events = [item for item in entries if str(item.get("entry_type") or "").lower() == "topup"]
    provider_pairs = {
        (
            str(item.get("provider") or "unknown").strip().lower() or "unknown",
            str(item.get("payment_method") or "unknown").strip().lower() or "unknown",
        )
        for item in payment_events
    }
    provider_churn = len(provider_pairs) / max(len(payment_events), 1)

    return {
        "recent_spend": recent_spend,
        "recent_payment_minor": recent_payment_minor,
        "recent_cashout": recent_cashout,
        "recent_shortfall": recent_shortfall,
        "spend_z": spend_z,
        "payment_z": payment_z,
        "velocity_z": velocity_z,
        "provider_churn": provider_churn,
        "dormant_gap_days": dormant_gap_days,
        "sequence_vectors": [
            [
                _clamp(_safe_float(item.get("debit_tokens")) / max(spend_mean + spend_std, 1.0), 0.0, 1.8),
                _clamp(_safe_float(item.get("payment_minor")) / max(payment_mean + payment_std, 1.0), 0.0, 1.8),
                _clamp(_safe_float(item.get("cashout_tokens")) / max(_safe_float(item.get("topup_tokens")) + 1.0, 1.0), 0.0, 1.8),
                _clamp(_safe_float(item.get("shortfall_tokens")) / max(_safe_float(item.get("debit_tokens")) + 1.0, 1.0), 0.0, 1.8),
                _clamp(_safe_float(item.get("event_count")) / max(velocity_mean + velocity_std, 1.0), 0.0, 1.8),
                _clamp(_safe_float(item.get("provider_count")) / 3.0, 0.0, 1.0),
            ]
            for item in points[-14:]
        ],
        "sample_count": len(points),
    }


def _fuzzy_score(metrics: Mapping[str, Any], snapshot: Mapping[str, Any], config: BillingFuzzyConfig) -> dict[str, Any]:
    spend_spike = _right_shoulder(_safe_float(metrics.get("spend_z")), 0.9, 2.8)
    payment_spike = _right_shoulder(_safe_float(metrics.get("payment_z")), 0.8, 2.5)
    velocity_spike = _right_shoulder(_safe_float(metrics.get("velocity_z")), 0.9, 2.4)
    cashout_ratio = _safe_float(metrics.get("recent_cashout")) / max(_safe_float(metrics.get("recent_spend")) + 1.0, 1.0)
    cashout_pressure = _right_shoulder(cashout_ratio, 0.18, 0.72)
    shortfall_ratio = _safe_float(snapshot.get("shortfall_total")) / max(
        _safe_float(snapshot.get("shortfall_total")) + _safe_float(snapshot.get("spent_total")) + 1.0,
        1.0,
    )
    shortfall_pressure = _right_shoulder(shortfall_ratio, 0.01, 0.12)
    provider_pressure = _right_shoulder(_safe_float(metrics.get("provider_churn")), 0.28, 0.92)
    dormancy_pressure = _triangle(_safe_float(metrics.get("dormant_gap_days")), 1.0, 5.0, 18.0)
    reserve_pressure = _right_shoulder(
        _safe_float(snapshot.get("reserved")) / max(_safe_float(snapshot.get("balance")) + 1.0, 1.0),
        0.18,
        0.74,
    )
    grant_dependency = _right_shoulder(
        _safe_float(snapshot.get("free_grant_total"))
        / max(_safe_float(snapshot.get("balance")) + _safe_float(snapshot.get("spent_total")) + 1.0, 1.0),
        0.26,
        0.82,
    )

    novelty_membership = max(spend_spike, payment_spike, velocity_spike, dormancy_pressure)
    metric_membership = _clamp((spend_spike + payment_spike + velocity_spike) / 3.0)
    security_membership = _clamp(max(cashout_pressure, shortfall_pressure, reserve_pressure * 0.72, provider_pressure * 0.62))

    normal = _left_shoulder(_safe_float(metrics.get("spend_z")), 0.2, 1.0)
    suspicious = _triangle(_safe_float(metrics.get("spend_z")), 0.8, 1.8, 3.2)
    anomalous = _right_shoulder(_safe_float(metrics.get("spend_z")), 1.8, 3.3)

    anomaly_strength = max(anomalous, suspicious * 0.72 + novelty_membership * 0.28)
    contextual = _clamp(
        0.25 * novelty_membership
        + 0.20 * security_membership
        + 0.16 * metric_membership
        + 0.16 * provider_pressure
        + 0.13 * dormancy_pressure
        + 0.10 * grant_dependency
    )
    suppressed = anomaly_strength * (1.0 - normal * 0.35)
    security_pull = _clamp(config.security_weight * security_membership * 0.28, 0.0, 0.28)
    metric_pull = _clamp(metric_membership * 0.18, 0.0, 0.18)
    base_risk = _clamp(
        suppressed * (0.72 + security_pull + metric_pull * 0.45)
        + contextual * (0.28 + security_pull * 0.65 + metric_pull)
    )

    if not config.enabled:
        return {
            "risk": base_risk,
            "confidence": _clamp(_safe_float(metrics.get("sample_count")) / 12.0),
            "order": 0,
            "interval_width": 0.0,
        }

    order = max(int(config.order), 1)
    center = base_risk
    span = _clamp(
        config.uncertainty
        * (0.45 * (1.0 - _clamp(_safe_float(metrics.get("sample_count")) / 18.0))
        + 0.30 * novelty_membership
        + 0.25 * (1.0 - abs(2.0 * base_risk - 1.0))),
        0.0,
        0.85,
    ) * 0.5
    interval_width = 0.0
    aarnn_context = _clamp(0.65 * dormancy_pressure + 0.35 * provider_pressure)
    context_bias = _clamp(
        novelty_membership * config.edge_bias
        + security_membership * config.security_weight
        + metric_membership * 0.34
        + aarnn_context * config.aarnn_weight
    )

    for layer in range(2, order + 1):
        decay = 1.0 / layer
        interval_width = _clamp(span * 2.0)
        lower = _clamp(center - span)
        upper = _clamp(center + span)
        softened = ((lower + upper) / 2.0) * (1.0 - decay * 0.28)
        pushed = ((lower * (1.0 - context_bias)) + (upper * context_bias))
        center = _clamp(softened * (1.0 - decay) + pushed * decay)
        span = _clamp(
            span * (0.70 - decay * 0.08)
            + context_bias * 0.03
            + security_membership * 0.015
            + aarnn_context * 0.01,
            0.0,
            0.5,
        )

    learned_confidence = _clamp(_safe_float(metrics.get("sample_count")) / 18.0)
    confidence = _clamp(learned_confidence * 0.70 + (1.0 - interval_width * 1.2) * 0.30)
    return {
        "risk": center,
        "confidence": confidence,
        "order": order,
        "interval_width": interval_width,
    }


def assess_account_anomaly(
    snapshot: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    *,
    daily_points: Sequence[Mapping[str, Any]],
    config: BillingAnomalyConfig | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    config = config or BillingAnomalyConfig()
    now = now or datetime.now(timezone.utc)
    metrics = _build_daily_metrics(entries, daily_points)
    fuzzy = _fuzzy_score(metrics, snapshot, config.fuzzy)
    neuro = NeuromimicPulseModel(config.neuro).score(metrics.get("sequence_vectors") or [])

    final_risk = _clamp(
        fuzzy["risk"] * (1.0 - config.fuzzy.aarnn_weight)
        + neuro["risk"] * config.fuzzy.aarnn_weight
    )
    confidence = _clamp(fuzzy["confidence"] * 0.72 + (1.0 - abs(neuro["activation"] - neuro["drift_score"])) * 0.28)

    if final_risk >= 0.80:
        posture = "intervene"
        tone = "danger"
    elif final_risk >= 0.62:
        posture = "review"
        tone = "warn"
    elif final_risk >= 0.38:
        posture = "observe"
        tone = "watch"
    else:
        posture = "clear"
        tone = "ok"

    signals = [
        {
            "key": "spend_spike",
            "label": "Usage acceleration",
            "value": round(_safe_float(metrics.get("spend_z")), 2),
            "score": round(_right_shoulder(_safe_float(metrics.get("spend_z")), 0.9, 2.8), 4),
            "triggered": _safe_float(metrics.get("spend_z")) >= 1.3,
        },
        {
            "key": "payment_spike",
            "label": "Settlement spike",
            "value": round(_safe_float(metrics.get("payment_z")), 2),
            "score": round(_right_shoulder(_safe_float(metrics.get("payment_z")), 0.8, 2.5), 4),
            "triggered": _safe_float(metrics.get("payment_z")) >= 1.25,
        },
        {
            "key": "provider_churn",
            "label": "Provider churn",
            "value": round(_safe_float(metrics.get("provider_churn")), 2),
            "score": round(_right_shoulder(_safe_float(metrics.get("provider_churn")), 0.28, 0.92), 4),
            "triggered": _safe_float(metrics.get("provider_churn")) >= 0.40,
        },
        {
            "key": "shortfall_pressure",
            "label": "Shortfall pressure",
            "value": round(
                _safe_float(snapshot.get("shortfall_total"))
                / max(_safe_float(snapshot.get("shortfall_total")) + _safe_float(snapshot.get("spent_total")) + 1.0, 1.0),
                3,
            ),
            "score": round(
                _right_shoulder(
                    _safe_float(snapshot.get("shortfall_total"))
                    / max(_safe_float(snapshot.get("shortfall_total")) + _safe_float(snapshot.get("spent_total")) + 1.0, 1.0),
                    0.01,
                    0.12,
                ),
                4,
            ),
            "triggered": _safe_float(snapshot.get("shortfall_total")) > 0,
        },
        {
            "key": "cashout_pressure",
            "label": "Cash-out pressure",
            "value": round(
                _safe_float(metrics.get("recent_cashout")) / max(_safe_float(metrics.get("recent_spend")) + 1.0, 1.0),
                3,
            ),
            "score": round(
                _right_shoulder(
                    _safe_float(metrics.get("recent_cashout")) / max(_safe_float(metrics.get("recent_spend")) + 1.0, 1.0),
                    0.18,
                    0.72,
                ),
                4,
            ),
            "triggered": _safe_float(metrics.get("recent_cashout")) > 0,
        },
        {
            "key": "neuromimic_drift",
            "label": "AARNN drift",
            "value": round(neuro["drift_score"], 3),
            "score": round(neuro["risk"], 4),
            "triggered": neuro["drift_score"] >= 0.48,
        },
    ]
    signals.sort(key=lambda item: item["score"], reverse=True)

    triggered = [item for item in signals if item["triggered"]][:3]
    if triggered:
        summary = " | ".join(
            f"{item['label']} is elevated ({item['value']})" for item in triggered
        )
    else:
        summary = "Activity remains inside NeuralMimicry's learned billing envelope."

    actions = list(dict.fromkeys(fuzzy_actions(posture) + neuro.get("actions", [])))
    anomalous = final_risk >= config.min_risk and confidence >= config.min_confidence
    if anomalous and "Trigger operator review" not in actions:
        actions.insert(0, "Trigger operator review")

    return {
        "generated_at": now.isoformat(),
        "risk": round(final_risk, 4),
        "confidence": round(confidence, 4),
        "posture": posture,
        "tone": tone,
        "anomalous": anomalous,
        "summary": summary,
        "signals": signals,
        "fuzzy": {
            "risk": round(fuzzy["risk"], 4),
            "confidence": round(fuzzy["confidence"], 4),
            "order": int(fuzzy["order"]),
            "interval_width": round(fuzzy["interval_width"], 4),
        },
        "ai": {
            "risk": round(neuro["risk"], 4),
            "drift_score": round(neuro["drift_score"], 4),
            "activation": round(neuro["activation"], 4),
            "state_vector": neuro["state_vector"],
        },
        "recommended_actions": actions,
    }


def fuzzy_actions(posture: str) -> list[str]:
    if posture == "intervene":
        return [
            "Pause manual cash-out processing",
            "Require step-up settlement verification",
            "Inspect recent service-linked debit bursts",
        ]
    if posture == "review":
        return [
            "Review the next settlement before release",
            "Compare payment source changes against prior history",
        ]
    if posture == "observe":
        return ["Continue observation", "Track the next billing period for drift"]
    return ["Continue observation"]
