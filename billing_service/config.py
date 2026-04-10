from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict

from .dashboard_anomaly import BillingAnomalyConfig, BillingFuzzyConfig, BillingNeuroConfig


def env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return default


def env_bool(*names: str, default: bool = False) -> bool:
    raw = env_first(*names, default="")
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def env_int(*names: str, default: int) -> int:
    raw = env_first(*names, default="")
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def env_float(*names: str, default: float) -> float:
    raw = env_first(*names, default="")
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def parse_app_tokens(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for part in (raw or "").split(","):
        chunk = part.strip()
        if not chunk:
            continue
        if "=" in chunk:
            app_id, token = chunk.split("=", 1)
        elif ":" in chunk:
            app_id, token = chunk.split(":", 1)
        else:
            continue
        app_id = app_id.strip()
        token = token.strip()
        if app_id and token:
            values[app_id] = token
    return values


@dataclass(slots=True)
class Settings:
    service_name: str
    version: str
    host: str
    port: int
    btc_rate: float
    app_tokens: Dict[str, str]
    require_customers: bool
    auth_open: bool
    dashboard_customer_ledger_limit: int
    dashboard_admin_block_limit: int
    dashboard_admin_account_limit: int
    dashboard_asset_cache_seconds: int
    anomaly_fuzzy_enabled: bool
    anomaly_fuzzy_order: int
    anomaly_fuzzy_uncertainty: float
    anomaly_fuzzy_edge_bias: float
    anomaly_fuzzy_aarnn_weight: float
    anomaly_fuzzy_security_weight: float
    anomaly_neuro_recurrence: float
    anomaly_neuro_drift_weight: float
    anomaly_neuro_activation_weight: float
    anomaly_neuro_novelty_weight: float
    anomaly_min_risk: float
    anomaly_min_confidence: float

    def anomaly_config(self) -> BillingAnomalyConfig:
        return BillingAnomalyConfig(
            fuzzy=BillingFuzzyConfig(
                enabled=self.anomaly_fuzzy_enabled,
                order=max(1, self.anomaly_fuzzy_order),
                uncertainty=max(0.0, self.anomaly_fuzzy_uncertainty),
                edge_bias=max(0.0, self.anomaly_fuzzy_edge_bias),
                aarnn_weight=max(0.0, self.anomaly_fuzzy_aarnn_weight),
                security_weight=max(0.0, self.anomaly_fuzzy_security_weight),
            ),
            neuro=BillingNeuroConfig(
                recurrence=max(0.0, self.anomaly_neuro_recurrence),
                drift_weight=max(0.0, self.anomaly_neuro_drift_weight),
                activation_weight=max(0.0, self.anomaly_neuro_activation_weight),
                novelty_weight=max(0.0, self.anomaly_neuro_novelty_weight),
            ),
            min_risk=max(0.0, self.anomaly_min_risk),
            min_confidence=max(0.0, self.anomaly_min_confidence),
        )

    @classmethod
    def from_env(cls) -> "Settings":
        btc_raw = env_first("BILLING_TOKEN_BTC_RATE", "REFINER_TOKEN_BTC_RATE", default="0.000016")
        try:
            btc_rate = float(btc_raw)
        except Exception:
            btc_rate = 0.000016
        return cls(
            service_name=env_first("BILLING_SERVICE_NAME", default="billing"),
            version=env_first("BILLING_VERSION", default="0.1.0"),
            host=env_first("BILLING_HOST", default="0.0.0.0"),
            port=env_int("BILLING_PORT", default=5020),
            btc_rate=btc_rate,
            app_tokens=parse_app_tokens(env_first("BILLING_APP_TOKENS", default="")),
            require_customers=env_bool("BILLING_REQUIRE_CUSTOMERS", default=True),
            auth_open=env_bool("BILLING_AUTH_OPEN", default=False),
            dashboard_customer_ledger_limit=max(20, min(env_int("BILLING_DASHBOARD_LEDGER_LIMIT", default=120), 500)),
            dashboard_admin_block_limit=max(20, min(env_int("BILLING_DASHBOARD_BLOCK_LIMIT", default=80), 200)),
            dashboard_admin_account_limit=max(5, min(env_int("BILLING_DASHBOARD_ACCOUNT_LIMIT", default=40), 200)),
            dashboard_asset_cache_seconds=max(0, env_int("BILLING_DASHBOARD_ASSET_CACHE_SECONDS", default=3600)),
            anomaly_fuzzy_enabled=env_bool("BILLING_ANOMALY_FUZZY_ENABLED", default=True),
            anomaly_fuzzy_order=max(1, env_int("BILLING_ANOMALY_FUZZY_ORDER", default=3)),
            anomaly_fuzzy_uncertainty=env_float("BILLING_ANOMALY_FUZZY_UNCERTAINTY", default=0.55),
            anomaly_fuzzy_edge_bias=env_float("BILLING_ANOMALY_FUZZY_EDGE_BIAS", default=0.70),
            anomaly_fuzzy_aarnn_weight=env_float("BILLING_ANOMALY_FUZZY_AARNN_WEIGHT", default=0.22),
            anomaly_fuzzy_security_weight=env_float("BILLING_ANOMALY_FUZZY_SECURITY_WEIGHT", default=0.28),
            anomaly_neuro_recurrence=env_float("BILLING_ANOMALY_NEURO_RECURRENCE", default=0.42),
            anomaly_neuro_drift_weight=env_float("BILLING_ANOMALY_NEURO_DRIFT_WEIGHT", default=0.36),
            anomaly_neuro_activation_weight=env_float("BILLING_ANOMALY_NEURO_ACTIVATION_WEIGHT", default=0.34),
            anomaly_neuro_novelty_weight=env_float("BILLING_ANOMALY_NEURO_NOVELTY_WEIGHT", default=0.30),
            anomaly_min_risk=env_float("BILLING_ANOMALY_MIN_RISK", default=0.62),
            anomaly_min_confidence=env_float("BILLING_ANOMALY_MIN_CONFIDENCE", default=0.30),
        )
