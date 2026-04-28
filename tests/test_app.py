from __future__ import annotations

from datetime import datetime, timedelta, timezone

from billing_service.access import resolve_identity_service_access
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
                self._entry(now - timedelta(days=3, hours=6), "user", "alice", "transfer_out", -15, source="token_vault", from_user="alice", to_user="bob", transfer_id="transfer-1", note="team handoff"),
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
                self._entry(now - timedelta(days=3, hours=6), "user", "bob", "transfer_in", 15, source="token_vault", from_user="alice", to_user="bob", transfer_id="transfer-1", paid_tokens=15, free_tokens=0, note="team handoff"),
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
                        "ts": (now - timedelta(days=3, hours=6)).isoformat(),
                        "actor_app": "billing",
                        "request_id": "transfer-1",
                        "tx_id": "tx-transfer-alice-1",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "alice",
                            "entry_type": "transfer_out",
                            "delta": -15,
                            "meta": {"source": "token_vault", "from_user": "alice", "to_user": "bob", "transfer_id": "transfer-1", "note": "team handoff"},
                        },
                    },
                    {
                        "ts": (now - timedelta(days=3, hours=6)).isoformat(),
                        "actor_app": "billing",
                        "request_id": "transfer-1",
                        "tx_id": "tx-transfer-bob-1",
                        "event": {
                            "event": "token_mutation",
                            "account_scope": "user",
                            "account_id": "bob",
                            "entry_type": "transfer_in",
                            "delta": 15,
                            "meta": {"source": "token_vault", "from_user": "alice", "to_user": "bob", "transfer_id": "transfer-1", "paid_tokens": 15, "free_tokens": 0, "note": "team handoff"},
                        },
                    },
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
            "request_id": meta.get("payment_id") or meta.get("transfer_id") or f"{entry_type}:{account_id}:{int(ts.timestamp())}",
            "tx_id": f"tx:{account_id}:{entry_type}:{int(ts.timestamp())}",
            "meta": meta,
        }

    def account_snapshot(self, scope, account_id):
        return dict(self._snapshots[(scope, account_id)])

    def ledger_entries(self, scope, account_id, limit=50):
        return {"entries": list(self._ledger.get((scope, account_id), []))[:limit]}

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
        snapshot = self._snapshots[(scope, account_id)]
        meta = dict(meta or {})
        requested_delta = int(delta or 0)
        paid_balance = int(snapshot.get("paid_balance") or snapshot.get("balance") or 0)
        free_balance = int(snapshot.get("free_balance") or 0)
        reserved = int(snapshot.get("reserved") or 0)
        shortfall = 0

        if entry_type in {"topup", "refund"}:
            if requested_delta > 0:
                paid_balance += requested_delta
            else:
                requested_delta = 0
        elif entry_type == "grant":
            if requested_delta > 0:
                free_balance += requested_delta
            else:
                requested_delta = 0
        elif entry_type == "transfer_in":
            if requested_delta <= 0:
                requested_delta = abs(requested_delta)
            desired = abs(requested_delta)
            free_tokens = max(0, int(meta.get("free_tokens") or meta.get("free_used") or 0))
            free_tokens = min(free_tokens, desired)
            paid_tokens = max(0, int(meta.get("paid_tokens") or meta.get("paid_used") or (desired - free_tokens)))
            paid_tokens = min(paid_tokens, desired - free_tokens)
            paid_tokens += desired - free_tokens - paid_tokens
            free_balance += free_tokens
            paid_balance += paid_tokens
            requested_delta = free_tokens + paid_tokens
            meta["free_used"] = free_tokens
            meta["paid_used"] = paid_tokens
            meta["used_total"] = requested_delta
        elif entry_type == "transfer_out":
            if requested_delta >= 0:
                requested_delta = -abs(requested_delta or 0)
            desired = abs(requested_delta)
            available = max(0, paid_balance + free_balance - reserved)
            if desired > available:
                shortfall = desired
                meta["shortfall"] = shortfall
                meta["free_used"] = 0
                meta["paid_used"] = 0
                meta["used_total"] = 0
                requested_delta = 0
            else:
                free_used = min(free_balance, desired)
                free_balance -= free_used
                remaining = desired - free_used
                paid_used = min(paid_balance, remaining)
                paid_balance -= paid_used
                meta["free_used"] = free_used
                meta["paid_used"] = paid_used
                meta["used_total"] = free_used + paid_used
                requested_delta = -(free_used + paid_used)
        elif entry_type == "cashout":
            if requested_delta >= 0:
                requested_delta = -abs(requested_delta)
            desired = abs(requested_delta)
            paid_used = min(paid_balance, desired)
            paid_balance -= paid_used
            shortfall = desired - paid_used
            if shortfall:
                meta["shortfall"] = shortfall
            meta["paid_used"] = paid_used
            meta["free_used"] = 0
            meta["used_total"] = paid_used
            requested_delta = -paid_used
        elif entry_type == "debit":
            if requested_delta >= 0:
                requested_delta = -abs(requested_delta or 0)
            desired = abs(requested_delta)
            free_used = min(free_balance, desired)
            free_balance -= free_used
            remaining = desired - free_used
            paid_used = min(paid_balance, remaining)
            paid_balance -= paid_used
            shortfall = remaining - paid_used
            if shortfall:
                meta["shortfall"] = shortfall
            meta["free_used"] = free_used
            meta["paid_used"] = paid_used
            meta["used_total"] = free_used + paid_used
            requested_delta = -(free_used + paid_used)

        final_type = entry_type if requested_delta or entry_type in {"reserve", "release", "sync"} else "adjust"
        balance_after = max(0, paid_balance + free_balance)
        snapshot["paid_balance"] = paid_balance
        snapshot["free_balance"] = free_balance
        snapshot["balance"] = balance_after
        snapshot["available"] = max(0, balance_after - reserved)
        snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
        snapshot["status"] = "low" if snapshot["available"] <= max(0, int(snapshot.get("last_topup_tokens") or balance_after) // 5) else "ok"
        if final_type == "debit":
            snapshot["spent_total"] = int(snapshot.get("spent_total") or 0) + int(meta.get("used_total") or abs(requested_delta) or 0)
            snapshot["shortfall_total"] = int(snapshot.get("shortfall_total") or 0) + shortfall
        if final_type == "cashout":
            snapshot["cashout_total"] = int(snapshot.get("cashout_total") or 0) + abs(requested_delta)
        if final_type == "grant":
            snapshot["free_grant_total"] = int(snapshot.get("free_grant_total") or 0) + abs(requested_delta)
        meta["paid_after"] = paid_balance
        meta["free_after"] = free_balance
        entry = self._entry(datetime.now(timezone.utc), scope, account_id, final_type, requested_delta, shortfall=shortfall, **meta)
        if request_id:
            entry["request_id"] = request_id
        self._ledger.setdefault((scope, account_id), []).append(entry)
        return {
            "entry": {
                "scope": scope,
                "account_id": account_id,
                "entry_type": entry["entry_type"],
                "delta": entry["delta"],
                "shortfall": shortfall,
                "balance_after": balance_after,
                "request_id": entry["request_id"],
                "meta": entry["meta"],
            },
            "snapshot": dict(snapshot),
        }

    def capture_payment(self, user_id, *, tokens, request_id=None, meta=None, **_kwargs):
        snapshot = self._snapshots[("user", user_id)]
        tokens = int(tokens)
        snapshot["paid_balance"] = int(snapshot.get("paid_balance") or snapshot.get("balance") or 0) + tokens
        snapshot["balance"] = int(snapshot.get("balance") or 0) + tokens
        snapshot["available"] = int(snapshot.get("available") or 0) + tokens
        snapshot["last_topup_tokens"] = tokens
        snapshot["updated_at"] = datetime.now(timezone.utc).isoformat()
        entry = self._entry(datetime.now(timezone.utc), "user", user_id, "topup", tokens, **(meta or {}))
        if request_id:
            entry["request_id"] = request_id
        self._ledger.setdefault(("user", user_id), []).append(entry)
        return {
            "entry": {
                "scope": "user",
                "account_id": user_id,
                "entry_type": "topup",
                "delta": tokens,
                "balance_after": int(snapshot.get("balance") or 0),
                "request_id": entry["request_id"],
                "meta": entry["meta"],
            },
            "snapshot": dict(snapshot),
        }


class FakeCustomersClient:
    def __init__(self, *, users=None, passwords=None, sessions=None):
        self._users = {
            username: {
                **(payload or {}),
                "authenticated": True,
                "user": username,
                "role": (payload or {}).get("role", "user"),
                "groups": (payload or {}).get("groups", [(payload or {}).get("role", "user")]),
                "user_record": {"username": username, **(payload or {})},
            }
            for username, payload in (users or {}).items()
        }
        self._passwords = dict(passwords or {})
        self._sessions = dict(sessions or {})

    def resolve_session(self, *, authorization=None, cookie_header=None):
        del cookie_header
        raw = str(authorization or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        return dict(self._sessions.get(raw) or {"authenticated": False, "user": None, "role": None})

    def verify_credentials(self, username, password):
        if self._passwords.get(username) != password:
            return {"authenticated": False, "user": None, "role": None}
        return self._users.get(username) or {"authenticated": True, "user": username, "role": "user", "groups": ["user"]}

    def get_user(self, username):
        return self._users.get(username, {"error": "user_not_found"})


def _build_app(monkeypatch, *, auth_open=True, customers_client=None):
    monkeypatch.setenv("BILLING_APP_TOKENS", "refiner=test-refiner-token")
    monkeypatch.setenv("BILLING_AUTH_OPEN", "1" if auth_open else "0")
    monkeypatch.setenv("BILLING_REQUIRE_CUSTOMERS", "0")
    monkeypatch.setenv("BILLING_CHAIN_API_BASE", "http://nmchain.test")
    monkeypatch.setenv("BILLING_CHAIN_API_TOKEN", "test-chain-token")
    app = create_app()
    app.extensions["nm_chain"] = FakeChain()
    app.extensions["customers_client"] = customers_client
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


def test_internal_routes_accept_trusted_service_account_tokens(monkeypatch):
    customers_client = FakeCustomersClient(
        sessions={
            "svc-refiner": {
                "authenticated": True,
                "identity_type": "service_account",
                "service_account_id": "refiner",
                "service_key": "refiner",
                "user": "refiner",
                "role": "service_account",
                "groups": ["admin"],
            },
            "svc-tracey": {
                "authenticated": True,
                "identity_type": "service_account",
                "service_account_id": "tracey",
                "service_key": "tracey",
                "user": "tracey",
                "role": "service_account",
                "groups": ["admin"],
            },
        }
    )
    app = _build_app(monkeypatch, auth_open=False, customers_client=customers_client)
    client = app.test_client()

    trusted_response = client.get(
        "/api/internal/accounts/user/alice",
        headers={"Authorization": "Bearer svc-refiner"},
    )
    assert trusted_response.status_code == 200
    assert trusted_response.get_json()["balance"] == 125

    forbidden_response = client.get(
        "/api/internal/accounts/user/alice",
        headers={"Authorization": "Bearer svc-tracey"},
    )
    assert forbidden_response.status_code == 403
    assert forbidden_response.get_json()["error"] == "forbidden"


def test_service_accounts_do_not_receive_default_billing_access():
    service_access = resolve_identity_service_access(
        {
            "authenticated": True,
            "identity_type": "service_account",
            "user": "tracey-sync",
            "role": "service_account",
            "groups": ["ops"],
            "service_access": {},
        }
    )

    billing = service_access["billing"]
    assert billing["access_level"] == "none"
    assert billing["visible"] is False
    assert billing["can_use"] is False


def test_user_can_transfer_tokens_to_another_user(monkeypatch):
    customers_client = FakeCustomersClient(
        users={"alice": {"role": "user"}, "bob": {"role": "user"}},
        passwords={"alice": "correct horse battery staple"},
    )
    app = _build_app(monkeypatch, customers_client=customers_client)
    client = app.test_client()

    response = client.post(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
        json={
            "action": "transfer",
            "username": "alice",
            "recipient": "bob",
            "password": "correct horse battery staple",
            "token_amount": 15,
            "note": "share tokens",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["balance"] == 110
    assert payload["free_balance"] == 10
    assert payload["recipient"]["snapshot"]["balance"] == 55
    assert payload["transfer"]["to_user"] == "bob"
    assert payload["transfer"]["free_tokens"] == 15
    assert payload["transfer"]["paid_tokens"] == 0

    alice_ledger = app.extensions["nm_chain"].ledger_entries("user", "alice", limit=20)["entries"]
    bob_ledger = app.extensions["nm_chain"].ledger_entries("user", "bob", limit=20)["entries"]
    assert alice_ledger[-1]["entry_type"] == "transfer_out"
    assert alice_ledger[-1]["meta"]["to_user"] == "bob"
    assert bob_ledger[-1]["entry_type"] == "transfer_in"
    assert bob_ledger[-1]["meta"]["from_user"] == "alice"


def test_transfer_rejects_negative_amounts(monkeypatch):
    customers_client = FakeCustomersClient(
        users={"alice": {"role": "user"}, "bob": {"role": "user"}},
        passwords={"alice": "secret"},
    )
    app = _build_app(monkeypatch, customers_client=customers_client)
    client = app.test_client()

    response = client.post(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
        json={
            "action": "transfer",
            "username": "alice",
            "recipient": "bob",
            "password": "secret",
            "token_amount": -5,
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_amount"


def test_transfer_rejects_insufficient_available_balance(monkeypatch):
    customers_client = FakeCustomersClient(
        users={"alice": {"role": "user"}, "bob": {"role": "user"}},
        passwords={"alice": "secret"},
    )
    app = _build_app(monkeypatch, customers_client=customers_client)
    client = app.test_client()

    response = client.post(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
        json={
            "action": "transfer",
            "username": "alice",
            "recipient": "bob",
            "password": "secret",
            "token_amount": 500,
        },
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "insufficient_tokens"


def test_transfer_rejects_self_and_unknown_recipient(monkeypatch):
    customers_client = FakeCustomersClient(
        users={"alice": {"role": "user"}},
        passwords={"alice": "secret"},
    )
    app = _build_app(monkeypatch, customers_client=customers_client)
    client = app.test_client()

    self_response = client.post(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
        json={
            "action": "transfer",
            "username": "alice",
            "recipient": "alice",
            "password": "secret",
            "token_amount": 5,
        },
    )
    assert self_response.status_code == 400
    assert self_response.get_json()["error"] == "self_transfer_forbidden"

    missing_response = client.post(
        "/api/tokens",
        headers={"X-Debug-User": "alice", "X-Debug-Role": "user"},
        json={
            "action": "transfer",
            "username": "alice",
            "recipient": "bob",
            "password": "secret",
            "token_amount": 5,
        },
    )
    assert missing_response.status_code == 404
    assert missing_response.get_json()["error"] == "target_not_found"


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
    assert payload["summary"]["transfer_out_tokens_30d"] == 15
    assert payload["transactions"][0]["entry_type"] in {"cashout", "debit", "topup", "grant", "transfer_out"}
    assert any(item["entry_type"] == "transfer_out" for item in payload["transactions"])
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
    assert payload["portfolio"]["recent_transfer_in_tokens"] == 15
    assert payload["portfolio"]["recent_transfer_out_tokens"] == 15
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


def test_admin_dashboard_accepts_admin_group(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    response = client.get(
        "/api/billing/dashboard/admin",
        headers={
            "X-Debug-User": "operator",
            "X-Debug-Role": "user",
            "X-Debug-Groups": "user,admin",
        },
    )

    assert response.status_code == 200

    html_response = client.get(
        "/billing",
        headers={
            "X-Debug-User": "operator",
            "X-Debug-Role": "user",
            "X-Debug-Groups": "user,admin",
            "X-Debug-Active-Team": "platform",
        },
    )
    assert html_response.status_code == 200
    body = html_response.get_data(as_text=True)
    assert "Groups: user, admin" in body
    assert "Active team: platform" in body


def test_delegated_billing_control_can_access_admin_dashboard_and_grant(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    api_response = client.get(
        "/api/billing/dashboard/admin",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=control",
        },
    )
    assert api_response.status_code == 200

    html_response = client.get(
        "/billing/admin",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=control",
        },
    )
    assert html_response.status_code == 200

    grant_response = client.post(
        "/api/tokens",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=control",
        },
        json={
            "action": "grant",
            "password": "irrelevant-in-auth-open",
            "target_user": "bob",
            "token_amount": 10,
            "note": "delegated billing admin grant",
        },
    )
    assert grant_response.status_code == 200
    assert grant_response.get_json()["target"] == "bob"


def test_user_without_billing_use_is_blocked(monkeypatch):
    app = _build_app(monkeypatch)
    client = app.test_client()

    dashboard_response = client.get(
        "/api/billing/dashboard/customer",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=none",
        },
    )
    assert dashboard_response.status_code == 403
    assert dashboard_response.get_json()["error"] == "forbidden"

    tokens_response = client.get(
        "/api/tokens",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=none",
        },
    )
    assert tokens_response.status_code == 403
    assert tokens_response.get_json()["error"] == "forbidden"

    ledger_response = client.get(
        "/api/tokens/ledger",
        headers={
            "X-Debug-User": "alice",
            "X-Debug-Role": "user",
            "X-Debug-Service-Access": "billing=none",
        },
    )
    assert ledger_response.status_code == 403


def test_dashboard_html_redirects_when_unauthenticated(monkeypatch):
    app = _build_app(monkeypatch, auth_open=False)
    client = app.test_client()

    response = client.get("/billing")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/login?next=%2Fbilling")
