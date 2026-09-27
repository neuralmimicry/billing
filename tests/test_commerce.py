import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import pytest
import requests
from sqlalchemy import create_engine, select, update

import billing_service.app as billing
from billing_service.commerce import metadata, orders, refunds, sign, verified_fields
from billing_service.nmchain_client import NmChainError


class Ledger:
    def __init__(self):
        self.balance = 0
        self.events = {}
        self.fail = False
        self.lose_capture_response = False
        self.lose_debit_response = False

    def health(self):
        return {"features": ["atomic_cashout_v1", "idempotent_entry_v1"]}

    def account_snapshot(self, *args):
        return {"balance": self.balance, "paid_balance": self.balance, "available": self.balance}

    def capture_payment(self, user, **values):
        if self.fail:
            raise NmChainError("offline")
        key = values["request_id"]
        if key not in self.events:
            self.balance += values["tokens"]
            self.events[key] = values
        if self.lose_capture_response:
            self.lose_capture_response = False
            raise NmChainError("acknowledgement lost")
        return {"duplicate": True}

    def apply_token(self, scope, user, **values):
        if self.fail:
            raise NmChainError("offline")
        key = values["request_id"]
        if key not in self.events:
            delta = values["delta"]
            if delta < 0 and self.balance < -delta:
                delta = 0
            self.balance += delta
            self.events[key] = {"delta": delta, "shortfall": abs(values["delta"] - delta)}
        if self.lose_debit_response:
            self.lose_debit_response = False
            raise NmChainError("acknowledgement lost")
        return {"entry": self.events[key]}


@pytest.fixture
def commerce(monkeypatch, tmp_path):
    monkeypatch.delenv("BILLING_DATABASE_URL", raising=False)
    monkeypatch.setenv("BILLING_CARDSTREAM_MERCHANT_ID", "test-merchant")
    monkeypatch.setenv("BILLING_CARDSTREAM_SIGNATURE_KEY", "test-secret")
    monkeypatch.setenv("BILLING_CARDSTREAM_REFUNDS_ENABLED", "1")
    monkeypatch.setenv("BILLING_POLICY_VERSION", "test-v1")
    monkeypatch.setenv("BILLING_TERMS_URL", "https://neuralmimicry.ai/purchase-terms")
    monkeypatch.setenv("BILLING_REFUND_POLICY_URL", "https://neuralmimicry.ai/refund-policy")
    monkeypatch.setenv("BILLING_PRODUCTS_JSON", json.dumps([{"id": "test-pack", "name": "Test pack", "tokens": 100, "amount_minor": 2500, "currency": "GBP"}]))
    database_url = os.getenv("NM_COMMERCE_TEST_DATABASE_URL")
    if database_url and not database_url.endswith("/nm_commerce_test"):
        pytest.fail("PostgreSQL tests require an isolated database named nm_commerce_test")
    engine = create_engine(database_url or f"sqlite:///{tmp_path / 'commerce.db'}")
    if database_url:
        metadata.drop_all(engine)
    metadata.create_all(engine)
    app = billing.create_app()
    app.config["TESTING"] = True
    app.extensions["commerce_engine"] = engine
    ledger = Ledger()
    app.extensions["nm_chain"] = ledger
    customers = {"alice": {"authenticated": True, "user": "alice", "refund_hold_until": None}}

    class CustomerClient:
        def get_user(self, user):
            return customers.get(user, {})
        def payment_details_changed(self, user, event_id):
            assert event_id.startswith("cardstream-sale:")
            return {"status": "ok"}
    app.extensions["customers_client"] = CustomerClient()

    def identity():
        user = billing.request.headers.get("X-Test-User", "alice")
        if user == "anonymous":
            return None
        return {**customers.get(user, {}), "authenticated": True, "user": user,
                "service_access": {"billing": {"access_level": "control" if user == "operator" else "use"}}}
    monkeypatch.setattr(billing, "_identity_from_request", identity)
    monkeypatch.setattr(billing, "_verify_password", lambda *_: True)
    # No test may send a real financial request.
    monkeypatch.setattr(requests, "post", lambda *a, **k: pytest.fail("Unexpected network call"))
    yield app.test_client(), engine, ledger, customers
    engine.dispose()


def purchase(client, key=None, **extra):
    return client.post("/api/billing/checkout", json={"product_id": "test-pack", "accept_terms": True,
        "policy_version": "test-v1", "idempotency_key": key or str(uuid.uuid4()), **extra})


def callback(client, order_id, **overrides):
    fields = {"merchantID": "test-merchant", "orderRef": order_id, "amount": "2500", "currency": "826", "action": "SALE", "responseCode": "0", "xref": "payment-1", **overrides}
    fields["signature"] = sign(fields, "test-secret")
    return client.post("/api/billing/cardstream/callback", data=fields)


def paid_refund(client):
    order_id = purchase(client).get_json()["order"]["id"]
    assert callback(client, order_id).status_code == 200
    response = client.post(f"/api/billing/orders/{order_id}/refunds", json={"reason": "The service did not meet my needs."})
    assert response.status_code == 201
    return order_id, response.get_json()["refund"]["id"]


def issue(client, refund_id):
    return client.post(f"/api/billing/refunds/{refund_id}/issue", json={"confirm": True}, headers={"X-Test-User": "operator"})


def provider(monkeypatch, code="0"):
    calls = []
    def post(url, *, data, **kwargs):
        calls.append(data)
        assert url == "https://gateway.cardstream.com/direct/"
        assert data["xref"] == "payment-1"  # Original card, never caller-supplied payout details.
        values = {key: data[key] for key in ("merchantID", "orderRef", "amount", "currency", "action")}
        values.update(responseCode=code, xref="refund-1")
        values["signature"] = sign(values, "test-secret")
        response = requests.Response()
        response.status_code = 200
        response._content = urlencode(values).encode()
        return response
    monkeypatch.setattr(requests, "post", post)
    return calls


def test_checkout_pins_price_owner_and_reuses_intent(commerce):
    client, engine, ledger, _ = commerce
    key = str(uuid.uuid4())
    first = purchase(client, key, amount_minor=1, token_amount=999999, user_id="someone-else")
    second = purchase(client, key)
    assert first.status_code == second.status_code == 200
    assert first.get_json()["order"] == second.get_json()["order"]
    fields = first.get_json()["checkout"]["form_fields"]
    assert fields["amount"] == "2500"
    assert first.get_json()["order"]["tokens"] == 100
    assert ledger.balance == 0
    with engine.connect() as conn:
        row = conn.execute(select(orders)).mappings().one()
        assert row["user_id"] == "alice" and row["policy_version"] == "test-v1"
    assert "no-store" in first.headers["Cache-Control"]


@pytest.mark.parametrize("extra", [{"accept_terms": False}, {"policy_version": "old"}, {"product_id": "invented"}, {"idempotency_key": ""}])
def test_checkout_requires_product_consent_and_request_key(commerce, extra):
    assert purchase(commerce[0], **extra).status_code in {400, 409}


@pytest.mark.parametrize("action", ["add", "sync", "refund", "cashout"])
def test_browser_cannot_mint_tokens_or_fake_a_payout(commerce, action):
    client, _, ledger, _ = commerce
    response = client.post("/api/tokens", json={"action": action, "token_amount": 999999, "balance": 999999, "password": "anything"})
    assert response.status_code == 409
    assert ledger.events == {}


def test_redirect_and_unsigned_callback_never_credit(commerce):
    client, _, ledger, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    response = client.post(f"/api/billing/cardstream/return?order_id={order_id}", data={"responseCode": "0"})
    assert response.status_code == 303
    assert "order_id=" in response.headers["Location"]
    assert client.post("/api/billing/cardstream/callback", data={"responseCode": "0", "orderRef": order_id}).status_code == 400
    assert ledger.balance == 0


@pytest.mark.parametrize("changes", [{"amount": "1"}, {"currency": "840"}, {"merchantID": "wrong"}, {"action": "AUTH"}])
def test_even_signed_callbacks_must_match_purchase(commerce, changes):
    client, _, ledger, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    assert callback(client, order_id, **changes).status_code in {400, 409}
    assert ledger.balance == 0


def test_capture_recovers_lost_ledger_response_without_duplicate_tokens(commerce):
    client, _, ledger, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    ledger.lose_capture_response = True
    assert callback(client, order_id).status_code == 503
    assert callback(client, order_id).status_code == 200
    assert callback(client, order_id).status_code == 200
    assert ledger.balance == 100
    assert client.get(f"/api/billing/orders/{order_id}").get_json()["order"]["status"] == "paid"


def test_failed_payment_can_later_be_confirmed_but_not_downgraded(commerce):
    client, _, ledger, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    assert callback(client, order_id, responseCode="5").status_code == 200
    assert ledger.balance == 0
    assert callback(client, order_id).status_code == 200
    assert callback(client, order_id, responseCode="5").status_code == 200
    assert client.get(f"/api/billing/orders/{order_id}").get_json()["order"]["status"] == "paid"


def test_existing_payment_settles_when_sales_catalog_is_disabled(commerce, monkeypatch):
    client, _, ledger, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    monkeypatch.setenv("BILLING_PRODUCTS_JSON", "invalid")
    assert client.get('/api/billing/catalog').get_json()["available"] is False
    assert callback(client, order_id).status_code == 200
    assert ledger.balance == 100


def test_payment_reference_cannot_credit_two_orders(commerce):
    client, _, ledger, _ = commerce
    first = purchase(client).get_json()["order"]["id"]
    second = purchase(client).get_json()["order"]["id"]
    assert callback(client, first).status_code == 200
    assert callback(client, second).status_code == 503
    assert ledger.balance == 100


def test_customer_cannot_read_or_refund_another_order(commerce):
    client, _, _, _ = commerce
    order_id = purchase(client).get_json()["order"]["id"]
    headers = {"X-Test-User": "other"}
    assert client.get(f"/api/billing/orders/{order_id}", headers=headers).status_code == 404
    assert client.post(f"/api/billing/orders/{order_id}/refunds", headers=headers, json={"reason": "Please refund this payment."}).status_code == 404
    assert client.get("/api/billing/orders", headers=headers).get_json()["orders"] == []
    assert client.get("/api/billing/refunds/review").status_code == 403


def test_refund_request_is_durable_idempotent_and_does_not_move_money(commerce):
    client, engine, ledger, _ = commerce
    order_id, refund_id = paid_refund(client)
    again = client.post(f"/api/billing/orders/{order_id}/refunds", json={"reason": "Please refund this payment."})
    assert again.status_code == 200 and again.get_json()["refund"]["id"] == refund_id
    assert ledger.balance == 100
    with engine.connect() as conn:
        assert conn.execute(select(refunds)).mappings().one()["status"] == "requested"


def test_recent_details_change_blocks_refund_and_is_rechecked(commerce, monkeypatch):
    client, _, ledger, customers = commerce
    _, refund_id = paid_refund(client)
    customers["alice"]["refund_hold_until"] = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    assert issue(client, refund_id).status_code == 409
    assert ledger.balance == 100
    customers["alice"]["refund_hold_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    calls = provider(monkeypatch)
    assert issue(client, refund_id).get_json()["status"] == "refunded"
    assert len(calls) == 1 and ledger.balance == 0


def test_refund_submission_is_not_repeated_after_timeout(commerce, monkeypatch):
    client, _, ledger, _ = commerce
    _, refund_id = paid_refund(client)
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout()
    monkeypatch.setattr(requests, "post", timeout)
    assert issue(client, refund_id).get_json()["status"] == "reconciliation_required"
    assert issue(client, refund_id).get_json()["status"] == "reconciliation_required"
    assert len(calls) == 1 and ledger.balance == 0
    assert callback(client, refund_id, action="REFUND", xref="refund-1").status_code == 200
    assert issue(client, refund_id).get_json()["status"] == "refunded"
    assert ledger.balance == 0


def test_refund_recovers_interrupted_debit_without_double_debit(commerce, monkeypatch):
    client, _, ledger, _ = commerce
    _, refund_id = paid_refund(client)
    calls = provider(monkeypatch)
    ledger.lose_debit_response = True
    assert issue(client, refund_id).status_code == 503
    assert issue(client, refund_id).get_json()["status"] == "refunded"
    assert len(calls) == 1 and ledger.balance == 0


def test_failed_card_refund_restores_tokens_once(commerce, monkeypatch):
    client, _, ledger, _ = commerce
    _, refund_id = paid_refund(client)
    calls = provider(monkeypatch, "5")
    assert issue(client, refund_id).get_json()["status"] == "refund_failed"
    assert ledger.balance == 100
    assert callback(client, refund_id, action="REFUND", xref="refund-1", responseCode="5").status_code == 200
    assert len(calls) == 1 and ledger.balance == 100


def test_insufficient_paid_tokens_never_calls_card_provider(commerce):
    client, _, ledger, _ = commerce
    _, refund_id = paid_refund(client)
    ledger.balance = 10
    assert issue(client, refund_id).status_code == 409
    assert ledger.balance == 10


def test_temporary_password_and_untrusted_origins_are_blocked(commerce):
    client, _, ledger, customers = commerce
    assert client.post("/api/billing/checkout", json={}, headers={"Origin": "https://attacker.invalid"}).status_code == 403
    assert client.post("/api/billing/checkout", data={}).status_code == 415
    customers["alice"]["requires_password_change"] = True
    assert purchase(client).status_code == 403
    assert ledger.balance == 0


def test_partial_signature_must_cover_all_settlement_fields():
    fields = {"merchantID": "test-merchant", "orderRef": "o", "amount": "1", "currency": "826", "action": "SALE", "responseCode": "0", "xref": "p"}
    fields["signature"] = sign({"orderRef": "o"}, "secret") + "|orderRef"
    assert verified_fields(fields, "secret") is None


@pytest.mark.skipif(not os.getenv("NM_COMMERCE_TEST_DATABASE_URL"), reason="Requires isolated PostgreSQL")
def test_concurrent_checkout_callbacks_and_refund_approvals(commerce, monkeypatch):
    client, engine, ledger, _ = commerce
    key = str(uuid.uuid4())
    def buy(_):
        with client.application.test_client() as concurrent_client:
            response = purchase(concurrent_client, key)
            assert response.status_code == 200
            return response.get_json()["order"]["id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(buy, range(4)))
    assert len(set(ids)) == 1
    def confirm(_):
        with client.application.test_client() as concurrent_client:
            return callback(concurrent_client, ids[0]).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(confirm, range(4))) == [200] * 4
    assert ledger.balance == 100
    refund_id = client.post(f'/api/billing/orders/{ids[0]}/refunds', json={"reason": "Please refund this purchase."}).get_json()["refund"]["id"]
    calls = provider(monkeypatch)
    def approve(_):
        with client.application.test_client() as concurrent_client:
            return issue(concurrent_client, refund_id).status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert list(pool.map(approve, range(4))) == [200] * 4
    assert len(calls) == 1 and ledger.balance == 0
