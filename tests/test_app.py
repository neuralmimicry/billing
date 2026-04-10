from __future__ import annotations

from datetime import datetime, timedelta, timezone

from billing_service.app import create_app


class FakeChain:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self._snapshots = {
            ("user", "alice"): {
                "scope": "user",
                "identity": {"account_id": "alice"},
                "balance": 125,
                "paid_balance": 100,
                "free_balance": 25,
                "reserved": 5,
                "available": 120,
                "last_topup_tokens": 100,
                "updated_at": now.isoformat(),
                "last_topup_at": (now - timedelta(days=5)).isoformat(),
                "spent_total": 100,
                "cashout_total": 10,
                "shortfall_total": 0,
                "free_grant_total": 25,
                "status": "ok",
            },
            ("user", "bob"): {
                "scope": "user",
                "identity": {"account_id": "bob"},
                "balance": 40,
                "paid_balance": 40,
                "free_balance": 0,
                "reserved": 0,
                "available": 40,
                "last_topup_tokens": 80,
                "updated_at": now.isoformat(),
                "last_topup_at": (now - timedelta(days=2)).isoformat(),
                "spent_total": 240,
                "cashout_total": 80,
                "shortfall_total": 12,
                "free_grant_total": 0,
                "status": "low",
            },
            ("team", "omega"): {
                "scope": "team",
                "identity": {"account_id": "omega"},
                "balance": 500,
                "paid_balance": 500,
                "free_balance": 0,
                "reserved": 120,
                "available": 380,
                "last_topup_tokens": 300,
                "updated_at": now.isoformat(),
                "last_topup_at": (now - timedelta(days=3)).isoformat(),
                "spent_total": 900,
                "cashout_total": 0,
                "shortfall_total": 0,
                "free_grant_total": 0,
                "status": "ok",
            },
        }
        self._ledger = {
            ("user", "alice"): [
                self._entry(now - timedelta(days=7), "user", "alice", "grant", 25, source="admin", note="welcome"),
                self._entry(
                    now - timedelta(days=5),
                    "user",
                    "alice",
                    "topup",
                    100,
                    source="portal",
                    payment_provider="stripe",
                    payment_method="card",
                    amount_minor=8500,
                    payment_id="pay_alice_1",
                ),
                self._entry(now - timedelta(days=4), "user", "alice", "debit", -70, source="api", operation="refiner-job"),
                self._entry(now - timedelta(days=2), "user", "alice", "debit", -30, source="api", operation="nmstt-voice"),
                self._entry(now - timedelta(days=1), "user", "alice", "cashout", -10, source="portal", btc_address="bc1alice"),
            ],
            ("user", "bob"): [
                self._entry(
                    now - timedelta(days=2),
                    "user",
                    "bob",
                    "topup",
                    80,
                    source="portal",
                    payment_provider="stripe",
                    payment_method="bank_transfer",
                    amount_minor=6400,
                    payment_id="pay_bob_1",
                ),
                self._entry(now - timedelta(days=1, hours=12), "user", "bob", "debit", -160, source="api", operation="refiner-batch"),
                self._entry(now - timedelta(days=1), "user", "bob", "cashout", -80, source="portal", btc_address="bc1bob"),
                self._entry(now - timedelta(hours=12), "user", "bob", "debit", -60, source="api", operation="nmstt-voice", shortfall=12),
            ],
            ("team", "omega"): [
                self._entry(now - timedelta(days=3), "team", "omega", "topup", 300, source="portal", payment_provider="adyen", payment_method="invoice", amount_minor=22500, payment_id="pay_team_1"),
                self._entry(now - timedelta(days=2), "team", "omega", "debit", -220, source="api", operation="workspace-run"),
                self._entry(now - timedelta(days=1), "team", "omega", "reserve", -120, source="api", operation="workspace-reserve"),
            ],
        }
        self._blocks = [
            {
                "index": 2,
                "ts": (now - timedelta(days=2)).isoformat(),
                "transactions": [
                    {
                        "ts": (now - timedelta(days=2)).isoformat(),
                        "actor_app": "billing",
                        "request_id": "pay_bob_1",
                        "tx_id": "tx-pay-bob",
                        "event": {
                            "event": "payment_captured",
                            "user_id": "bob",
                            "tokens": 80,
                            "amount_minor": 6400,
                            "currency": "GBP",
                            "provider": "stripe",
                            "payment_id": "pay_bob_1",
                            "checkout_flow": "portal",
                            "meta": {"payment_method": "bank_transfer", "source": "portal"},
                        },
                    },
                    {
                        "ts": (now - timedelta(days=2)).isoformat(),
                        "actor_app": "refiner",
                        "request_id": "job-bob-1",
                        "tx_id": "tx-bob-1",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "bob",
                            "entry_type": "debit",
                            "delta": -160,
                            "meta": {"source": "api", "operation": "refiner-batch"},
                        },
                    },
                ],
            },
            {
                "index": 3,
                "ts": (now - timedelta(days=1)).isoformat(),
                "transactions": [
                    {
                        "ts": (now - timedelta(days=1)).isoformat(),
                        "actor_app": "billing",
                        "request_id": "pay_team_1",
                        "tx_id": "tx-team-1",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "team",
                            "account_id": "omega",
                            "entry_type": "topup",
                            "delta": 300,
                            "meta": {
                                "source": "portal",
                                "payment_provider": "adyen",
                                "payment_method": "invoice",
                                "amount_minor": 22500,
                                "payment_id": "pay_team_1",
                            },
                        },
                    },
                    {
                        "ts": (now - timedelta(days=1)).isoformat(),
                        "actor_app": "refiner",
                        "request_id": "job-omega-1",
                        "tx_id": "tx-team-2",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "team",
                            "account_id": "omega",
                            "entry_type": "debit",
                            "delta": -220,
                            "meta": {"source": "api", "operation": "workspace-run"},
                        },
                    },
                    {
                        "ts": (now - timedelta(days=1)).isoformat(),
                        "actor_app": "billing",
                        "request_id": "cashout-bob-1",
                        "tx_id": "tx-bob-2",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "bob",
                            "entry_type": "cashout",
                            "delta": -80,
                            "meta": {"source": "portal", "btc_address": "bc1bob"},
                        },
                    },
                ],
            },
            {
                "index": 4,
                "ts": now.isoformat(),
                "transactions": [
                    {
                        "ts": now.isoformat(),
                        "actor_app": "billing",
                        "request_id": "pay_alice_1",
                        "tx_id": "tx-pay-alice",
                        "event": {
                            "event": "payment_captured",
                            "user_id": "alice",
                            "tokens": 100,
                            "amount_minor": 8500,
                            "currency": "GBP",
                            "provider": "stripe",
                            "payment_id": "pay_alice_1",
                            "checkout_flow": "portal",
                            "meta": {"payment_method": "card", "source": "portal"},
                        },
                    },
                    {
                        "ts": (now - timedelta(hours=10)).isoformat(),
                        "actor_app": "refiner",
                        "request_id": "job-alice-1",
                        "tx_id": "tx-alice-1",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "alice",
                            "entry_type": "debit",
                            "delta": -70,
                            "meta": {"source": "api", "operation": "refiner-job"},
                        },
                    },
                    {
                        "ts": (now - timedelta(hours=6)).isoformat(),
                        "actor_app": "nmstt",
                        "request_id": "voice-alice-1",
                        "tx_id": "tx-alice-2",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "alice",
                            "entry_type": "debit",
                            "delta": -30,
                            "meta": {"source": "api", "operation": "nmstt-voice"},
                        },
                    },
                    {
                        "ts": (now - timedelta(hours=2)).isoformat(),
                        "actor_app": "refiner",
                        "request_id": "job-bob-2",
                        "tx_id": "tx-bob-3",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "bob",
                            "entry_type": "debit",
                            "delta": -60,
                            "meta": {"source": "api", "operation": "nmstt-voice", "shortfall": 12},
                        },
                    },
                ],
            },
        ]

    @staticmethod
    def _entry(ts, scope, account_id, entry_type, delta, shortfall=0, **meta):
        return {
            "ts": ts.isoformat(),
            "scope": scope,
            "account_scope": scope,
            "account_id": account_id,
            "entry_type": entry_type,
            "delta": delta,
            "shortfall": shortfall,
            "actor_app": meta.get("source") == "api" and "refiner" or "billing",
            "request_id": meta.get("payment_id") or f"{entry_type}:{account_id}:{int(ts.timestamp())}",
            "tx_id": f"tx:{account_id}:{entry_type}:{int(ts.timestamp())}",
            "meta": meta,
        }

    def account_snapshot(self, scope, account_id):
        return self._snapshots[(scope, account_id)]

    def ledger_entries(self, scope, account_id, limit=50):
        return {
            "entries": list(self._ledger.get((scope, account_id), []))[:limit],
        }

    def chain_status(self):
        return {
            "chain": {
                "chain_id": "nm-billing-ledger",
                "height": len(self._blocks),
                "head_hash": "abc123head",
                "validator_id": "validator-a",
                "auth_mode": "bearer",
                "account_count": len(self._snapshots),
            }
        }

    def list_blocks(self, limit=20):
        return {"blocks": list(self._blocks)[-limit:]}

    def apply_token(self, scope, account_id, *, entry_type, delta, request_id=None, meta=None):
        snapshot = self.account_snapshot(scope, account_id)
        return {
            "entry": {
                "scope": scope,
                "account_id": account_id,
                "entry_type": entry_type,
                "delta": delta,
                "balance_after": int(snapshot.get("balance") or 0) + int(delta),
                "request_id": request_id,
                "meta": meta or {},
            },
            "snapshot": snapshot,
        }

    def capture_payment(self, user_id, *, tokens, request_id=None, meta=None, **_kwargs):
        snapshot = self.account_snapshot("user", user_id)
        return {
            "entry": {
                "scope": "user",
                "account_id": user_id,
                "entry_type": "payment",
                "delta": int(tokens),
                "balance_after": int(snapshot.get("balance") or 0) + int(tokens),
                "request_id": request_id,
                "meta": meta or {},
            },
            "snapshot": snapshot,
        }


def _build_app(monkeypatch, *, auth_open=True):
    monkeypatch.setenv("BILLING_APP_TOKENS", "refiner=test-refiner-token")
    monkeypatch.setenv("BILLING_AUTH_OPEN", "1" if auth_open else "0")
    monkeypatch.setenv("BILLING_REQUIRE_CUSTOMERS", "0")
    monkeypatch.setenv("BILLING_CHAIN_API_BASE", "http://nmchain.test")
    monkeypatch.setenv("BILLING_CHAIN_API_TOKEN", "test-chain-token")
    app = create_app()
    app.extensions["nm_chain"] = FakeChain()
    app.extensions["customers_client"] = None
    return app


def test_health_reports_dependencies(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    response = client.get("/api/health")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["service"] == "billing"
    assert payload["nmchain_enabled"] is True
    assert payload["auth_open"] is True


def test_public_and_internal_account_routes_remain_stable(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    public_response = client.get(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "admin"},
    )
    assert public_response.status_code == 200
    public_payload = public_response.get_json()
    assert public_payload["balance"] == 125
    assert public_payload["btc_rate"] == 0.000016

    forbidden_response = client.get("/api/internal/accounts/user/alice")
    assert forbidden_response.status_code == 403

    internal_response = client.get(
        "/api/internal/accounts/user/alice",
        headers={"Authorization": "Bearer test-refiner-token"},
    )
    assert internal_response.status_code == 200
    internal_payload = internal_response.get_json()
    assert internal_payload["balance"] == 125

    ledger_response = client.get(
        "/api/internal/accounts/user/alice/ledger",
        headers={"Authorization": "Bearer test-refiner-token"},
    )
    assert ledger_response.status_code == 200
    assert ledger_response.get_json()["entries"][0]["entry_type"] == "grant"


def test_customer_dashboard_routes(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    api_response = client.get(
        "/api/billing/dashboard/customer",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
    )
    assert api_response.status_code == 200
    payload = api_response.get_json()
    assert payload["scope"] == "customer"
    assert payload["summary"]["balance_tokens"] == 125
    assert payload["transactions"][0]["entry_type"] in {"cashout", "debit", "topup", "grant"}
    assert "anomaly" in payload

    html_response = client.get(
        "/billing",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
    )
    assert html_response.status_code == 200
    body = html_response.get_data(as_text=True)
    assert "NeuralMimicry Billing Intelligence" in body
    assert "/api/billing/dashboard/customer" in body

    asset_response = client.get("/billing/assets/dashboard.css")
    assert asset_response.status_code == 200
    assert "--nm-bg" in asset_response.get_data(as_text=True)


def test_admin_dashboard_routes(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    api_response = client.get(
        "/api/billing/dashboard/admin",
        headers={"X-Debug-User": "operator", "X-Debug-Role": "admin"},
    )
    assert api_response.status_code == 200
    payload = api_response.get_json()
    assert payload["scope"] == "admin"
    assert payload["chain"]["height"] == 3
    assert payload["top_accounts"]
    assert payload["anomaly_queue"]
    assert any(item["account_ref"].startswith("user/") for item in payload["top_accounts"])

    html_response = client.get(
        "/billing/admin",
        headers={"X-Debug-User": "operator", "X-Debug-Role": "admin"},
    )
    assert html_response.status_code == 200
    body = html_response.get_data(as_text=True)
    assert "NeuralMimicry Billing Control Plane" in body
    assert "/api/billing/dashboard/admin" in body

    forbidden_response = client.get(
        "/api/billing/dashboard/admin",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
    )
    assert forbidden_response.status_code == 403


def test_dashboard_html_redirects_when_unauthenticated(monkeypatch):
    app = _build_app(monkeypatch, auth_open=False)
    client = app.test_client()

    response = client.get("/billing")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=%2Fbilling")
