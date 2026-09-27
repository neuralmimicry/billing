"""Durable hosted checkout and customer refund review.

Money is never inferred from a browser redirect. Only a verified Cardstream
SALE callback can credit nmchain. Refund requests require explicit operator
approval, a fresh cooling-off check and an idempotent ledger withdrawal before
the original card can be refunded.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import uuid
import requests
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlencode, urlsplit, parse_qsl

import click
from flask import Blueprint, current_app, jsonify, redirect, request
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, UniqueConstraint, create_engine, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .access import can_control_billing, can_use_billing
from .customers_client import CustomersClientError
from .nmchain_client import NmChainError

metadata = MetaData()
orders = Table(
    "billing_orders", metadata,
    Column("id", String(36), primary_key=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("idempotency_key", String(80), nullable=False),
    Column("product_id", String(80), nullable=False),
    Column("product_name", String(120), nullable=False),
    Column("tokens", Integer, nullable=False),
    Column("amount_minor", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("status", String(32), nullable=False),
    Column("payment_reference", String(128), unique=True),
    Column("policy_version", String(80), nullable=False),
    Column("terms_url", Text, nullable=False),
    Column("refund_policy_url", Text, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    UniqueConstraint("user_id", "idempotency_key"),
)
refunds = Table(
    "billing_refund_requests", metadata,
    Column("id", String(36), primary_key=True),
    Column("order_id", String(36), nullable=False, unique=True),
    Column("user_id", String(128), nullable=False, index=True),
    Column("reason", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("response", Text, nullable=False),
    Column("reviewed_by", String(128)),
    Column("refund_reference", String(128), unique=True),
    Column("hold_until", String(40)),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
bp = Blueprint("commerce", __name__)


def now():
    return datetime.now(timezone.utc).isoformat()


def sign(fields, secret):
    """Cardstream PHP SDK signing: ASCII sort, RFC1738 query, LF newlines.

    Reference: https://github.com/cardstream/php-sdk/blob/main/gateway.php
    """
    pairs = [(key, str(value)) for key, value in fields.items() if key != "signature"]
    # PHP sorts top-level keys, preserving the order of nested form fields.
    encoded = urlencode(sorted(pairs, key=lambda pair: pair[0].split("[", 1)[0]))
    encoded = encoded.replace("~", "%7E")  # PHP http_build_query (RFC1738).
    encoded = re.sub(r"%0D%0A|%0A%0D|%0D", "%0A", encoded, flags=re.I)
    return hashlib.sha512((encoded + secret).encode()).hexdigest()


def verified_fields(fields, secret):
    signature, _, names = fields.get("signature", "").partition("|")
    signed = {key: value for key, value in fields.items() if key != "signature"}
    if names:
        selected = set(names.split(","))
        signed = {key: value for key, value in signed.items() if key.split("[", 1)[0] in selected}
    # Every settlement assertion must be covered even for partial signatures.
    if not {"merchantID", "orderRef", "amount", "currency", "action", "responseCode", "xref"} <= signed.keys():
        return None
    if not secret or not hmac.compare_digest(sign(signed, secret), signature):
        return None
    return signed


def _https_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("An HTTPS URL is required")
    return value


def _gateway_configuration():
    merchant = os.getenv("BILLING_CARDSTREAM_MERCHANT_ID", "").strip()
    secret = os.getenv("BILLING_CARDSTREAM_SIGNATURE_KEY", "").strip()
    try:
        site = _https_url(os.getenv("NEURALMIMICRY_SITE_BASE", "https://neuralmimicry.ai").rstrip("/"))
        api = _https_url(os.getenv("BILLING_PUBLIC_API_BASE", "https://api.neuralmimicry.ai").rstrip("/"))
        return dict(merchant=merchant, secret=secret, site=site, api=api)
    except ValueError:
        return None


def _configuration():
    gateway = _gateway_configuration()
    if gateway is None:
        return None
    try:
        terms = _https_url(os.getenv("BILLING_TERMS_URL", ""))
        policy = _https_url(os.getenv("BILLING_REFUND_POLICY_URL", ""))
        version = os.getenv("BILLING_POLICY_VERSION", "").strip()
        products = json.loads(os.getenv("BILLING_PRODUCTS_JSON", "[]"))
        if not isinstance(products, list) or not version or len(version) > 80:
            raise ValueError("Invalid catalogue")
        seen = set()
        for product in products:
            if not isinstance(product, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", product.get("id", "")):
                raise ValueError("Invalid product")
            if product["id"] in seen:
                raise ValueError("Duplicate product")
            seen.add(product["id"])
            if not isinstance(product.get("name"), str) or not 1 <= len(product["name"]) <= 120:
                raise ValueError("Invalid product name")
            for field in ("tokens", "amount_minor"):
                if type(product.get(field)) is not int or not 1 <= product[field] <= 100_000_000:
                    raise ValueError("Invalid product amount")
            if product.get("currency") != "GBP":
                raise ValueError("Only GBP checkout is supported")
        return dict(**gateway, terms=terms,
                    policy=policy, version=version, products=products)
    except (ValueError, TypeError):
        return None


def _engine():
    engine = current_app.extensions.get("commerce_engine")
    if engine is None:
        raise CommerceError("billing_unavailable", "Payments are temporarily unavailable. Please try again later.", 503)
    return engine


class CommerceError(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status


def customer_route(admin=False):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            from .app import _identity_from_request, _allowed_origin
            # JSON + an allowed browser Origin blocks cross-site form submissions.
            if request.method == "POST":
                if not request.is_json:
                    raise CommerceError("json_required", "Send a JSON request.", 415)
                if request.headers.get("Origin") and not _allowed_origin():
                    raise CommerceError("forbidden_origin", "This website cannot make account changes.", 403)
            identity = _identity_from_request()
            if not identity or not identity.get("user"):
                raise CommerceError("unauthorized", "Please sign in again.", 401)
            if identity.get("requires_password_change"):
                raise CommerceError("password_change_required", "Change your temporary password before continuing.", 403)
            if not (can_control_billing(identity) if admin else can_use_billing(identity)):
                raise CommerceError("forbidden", "Billing access is required.", 403)
            return fn(identity, *args, **kwargs)
        return wrapped
    return decorate


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise CommerceError("invalid_request", "A JSON object is required.")
    return payload


def public_order(row):
    return {key: row[key] for key in ("id", "product_id", "product_name", "tokens", "amount_minor", "currency", "status", "created_at", "updated_at")}


def checkout(row, config):
    fields = dict(merchantID=config["merchant"], action="SALE", type="1", countryCode="826", currency="826",
                  amount=str(row["amount_minor"]), orderRef=row["id"], transactionUnique=row["id"],
                  orderDescription=f"NeuralMimicry: {row['product_name']}",
                  redirectURL=f"{config['api']}/api/billing/cardstream/return?order_id={row['id']}",
                  callbackURL=f"{config['api']}/api/billing/cardstream/callback")
    fields["signature"] = sign(fields, config["secret"])
    return dict(form_action="https://gateway.cardstream.com/hosted/", form_method="POST", form_fields=fields)


@bp.get("/api/billing/catalog")
@customer_route()
def catalog(identity):
    config = _configuration()
    available = bool(config and config["merchant"] and config["secret"] and config["products"] and current_app.extensions.get("commerce_engine") is not None)
    if available:
        # Do not advertise a working checkout until its durable schema exists.
        with _engine().connect() as conn:
            conn.execute(select(orders.c.id).limit(1))
    return jsonify(available=available, products=config["products"] if available else [],
                   terms_url=config["terms"] if config else None,
                   refund_policy_url=config["policy"] if config else None,
                   policy_version=config["version"] if config else None)


@bp.post("/api/billing/checkout")
@customer_route()
def create_checkout(identity):
    config = _configuration()
    if not config or not config["merchant"] or not config["secret"]:
        raise CommerceError("checkout_unavailable", "Card payments are not available yet.", 503)
    payload = body()
    key = payload.get("idempotency_key", "")
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", key):
        raise CommerceError("invalid_request_id", "A checkout request ID is required.")
    if payload.get("accept_terms") is not True or payload.get("policy_version") != config["version"]:
        raise CommerceError("terms_required", "Please review and accept the current purchase terms.", 409)
    product = next((item for item in config["products"] if item["id"] == payload.get("product_id")), None)
    if product is None:
        raise CommerceError("invalid_product", "Choose an available token pack.")
    # Unique (user, key) serialises double clicks and retries across workers.
    def existing(conn):
        return conn.execute(select(orders).where(orders.c.user_id == identity["user"], orders.c.idempotency_key == key)).mappings().first()
    try:
        with _engine().begin() as conn:
            row = existing(conn)
            if row is None:
                row = dict(id=str(uuid.uuid4()), user_id=identity["user"], idempotency_key=key,
                           product_id=product["id"], product_name=product["name"], tokens=product["tokens"],
                           amount_minor=product["amount_minor"], currency=product["currency"], status="pending",
                           payment_reference=None, policy_version=config["version"], terms_url=config["terms"],
                           refund_policy_url=config["policy"], created_at=now(), updated_at=now())
                conn.execute(insert(orders).values(**row))
    except IntegrityError:
        with _engine().connect() as conn:
            row = existing(conn)
        if row is None:
            raise
    if row["product_id"] != product["id"] or row["policy_version"] != config["version"] or row["amount_minor"] != product["amount_minor"] or row["tokens"] != product["tokens"]:
        raise CommerceError("checkout_conflict", "Start a new checkout for this selection.", 409)
    return jsonify(order=public_order(row), checkout=checkout(row, config) if row["status"] == "pending" else None)


@bp.get("/api/billing/orders")
@customer_route()
def list_orders(identity):
    with _engine().connect() as conn:
        rows = conn.execute(select(orders).where(orders.c.user_id == identity["user"]).order_by(orders.c.created_at.desc()).limit(100)).mappings()
        result = [public_order(row) for row in rows]
        requests = [dict(row) for row in conn.execute(select(refunds).where(refunds.c.user_id == identity["user"]).order_by(refunds.c.created_at.desc()).limit(100)).mappings()]
    for item in requests:
        item.pop("reviewed_by", None)
    return jsonify(orders=result, refunds=requests)


@bp.get("/api/billing/orders/<order_id>")
@customer_route()
def order_status(identity, order_id):
    with _engine().connect() as conn:
        row = conn.execute(select(orders).where(orders.c.id == order_id, orders.c.user_id == identity["user"])).mappings().first()
    if row is None:
        raise CommerceError("order_not_found", "Payment not found.", 404)
    return jsonify(order=public_order(row))


@bp.post("/api/billing/cardstream/callback")
def payment_callback():
    config = _gateway_configuration()
    if not config or not config["merchant"] or not config["secret"]:
        raise CommerceError("checkout_unavailable", "Payments are not configured.", 503)
    if any(len(request.form.getlist(key)) != 1 for key in request.form):
        raise CommerceError("invalid_callback", "Duplicate callback fields.")
    fields = verified_fields(request.form.to_dict(), config["secret"])
    if fields is None:
        raise CommerceError("invalid_signature", "Invalid payment signature.", 400)
    if fields["merchantID"] != config["merchant"]:
        raise CommerceError("invalid_callback", "Unexpected payment type.")
    if fields["action"] == "REFUND":
        settle_refund_response(fields)
        return jsonify(status="ok")
    if fields["action"] != "SALE":
        raise CommerceError("invalid_callback", "Unexpected payment type.")
    with _engine().begin() as conn:
        row = conn.execute(select(orders).where(orders.c.id == fields["orderRef"]).with_for_update()).mappings().first()
        if row is None:
            raise CommerceError("order_not_found", "Payment not found.", 404)
        if fields["amount"] != str(row["amount_minor"]) or fields["currency"] != "826" or not fields["xref"] or len(fields["xref"]) > 128:
            raise CommerceError("payment_mismatch", "Payment does not match the order.", 409)
        if row["payment_reference"] and row["payment_reference"] != fields["xref"]:
            raise CommerceError("payment_conflict", "Payment reference changed.", 409)
        if row["status"] == "paid":
            return jsonify(status="ok")
        if fields["responseCode"] != "0":
            # A failed attempt does not invalidate a later authenticated success.
            conn.execute(update(orders).where(orders.c.id == row["id"]).values(status="failed", updated_at=now()))
            return jsonify(status="ok")
        from .app import _require_chain, _customers
        # Stable event identity makes a retry safe if the ledger commits but the
        # HTTP response or database commit is lost. Return 503 on ledger failure
        # so Cardstream can retry; never acknowledge an uncredited payment.
        conn.execute(update(orders).where(orders.c.id == row["id"]).values(payment_reference=fields["xref"]))
        # Hosted checkout may introduce new card details. Conservatively begin
        # the refund hold for each confirmed purchase; callback retries reuse the
        # same event and do not extend it. No card data is copied into Customers.
        customers = _customers()
        if customers is None or customers.payment_details_changed(row["user_id"], f"cardstream-sale:{row['id']}").get("status") != "ok":
            raise CommerceError("auth_unavailable", "Payment details could not be recorded safely.", 503)
        _require_chain().capture_payment(row["user_id"], tokens=row["tokens"], amount_minor=row["amount_minor"],
            currency=row["currency"], provider="cardstream", payment_id=fields["xref"], checkout_flow="hosted",
            request_id=f"cardstream-sale:{row['id']}", meta={"order_id": row["id"], "source": "verified_callback"})
        conn.execute(update(orders).where(orders.c.id == row["id"]).values(status="paid", updated_at=now()))
    return jsonify(status="ok")


@bp.route("/api/billing/cardstream/return", methods=["GET", "POST"])
def payment_return():
    # This redirect is navigational only and never writes settlement state.
    site = _https_url(os.getenv("NEURALMIMICRY_SITE_BASE", "https://neuralmimicry.ai").rstrip("/"))
    order_id = str(request.args.get("order_id", ""))
    try:
        order_id = str(uuid.UUID(order_id))
    except ValueError:
        order_id = ""
    return redirect(f"{site}/billing?{urlencode({'order_id': order_id})}", code=303)


@bp.post("/api/billing/orders/<order_id>/refunds")
@customer_route()
def request_refund(identity, order_id):
    reason = body().get("reason")
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 2000:
        raise CommerceError("reason_required", "Describe your refund request in 10–2,000 characters.")
    with _engine().begin() as conn:
        row = conn.execute(select(orders).where(orders.c.id == order_id, orders.c.user_id == identity["user"]).with_for_update()).mappings().first()
        if row is None:
            raise CommerceError("order_not_found", "Payment not found.", 404)
        if row["status"] != "paid":
            raise CommerceError("payment_not_confirmed", "A refund can be requested once payment is confirmed.", 409)
        existing = conn.execute(select(refunds).where(refunds.c.order_id == order_id)).mappings().first()
        if existing:
            return jsonify(refund=dict(existing)), 200
        hold = refund_hold(identity)
        record = dict(id=str(uuid.uuid4()), order_id=order_id, user_id=identity["user"], reason=reason.strip(),
                      status="requested", response="", reviewed_by=None, refund_reference=None,
                      hold_until=hold, created_at=now(), updated_at=now())
        conn.execute(insert(refunds).values(**record))
    return jsonify(refund=record), 201


@bp.get("/api/billing/refunds/review")
@customer_route(admin=True)
def refund_review_queue(identity):
    with _engine().connect() as conn:
        rows = conn.execute(select(refunds, orders.c.amount_minor, orders.c.currency, orders.c.tokens, orders.c.payment_reference)
                            .join(orders, orders.c.id == refunds.c.order_id).order_by(refunds.c.created_at.desc()).limit(200)).mappings()
        return jsonify(refunds=[dict(row) for row in rows])


@bp.post("/api/billing/refunds/<refund_id>/review")
@customer_route(admin=True)
def review_refund(identity, refund_id):
    payload = body()
    status, response = payload.get("status"), payload.get("response")
    if status not in {"under_review", "declined"} or not isinstance(response, str) or not 10 <= len(response.strip()) <= 2000:
        raise CommerceError("invalid_review", "Choose a review status and provide a customer-facing explanation.")
    with _engine().begin() as conn:
        row = conn.execute(select(refunds).where(refunds.c.id == refund_id).with_for_update()).mappings().first()
        if row is None:
            raise CommerceError("refund_not_found", "Refund request not found.", 404)
        if row["status"] not in {"requested", "under_review", "declined"}:
            raise CommerceError("refund_in_progress", "This refund has entered settlement and cannot be edited.", 409)
        conn.execute(update(refunds).where(refunds.c.id == refund_id).values(status=status, response=response.strip(), reviewed_by=identity["user"], updated_at=now()))
    return jsonify(status="ok")


def refund_hold(identity):
    value = identity.get("refund_hold_until")
    if not value:
        return None
    try:
        hold = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if hold.tzinfo is None:
            raise ValueError("Missing timezone")
        return value if hold > datetime.now(timezone.utc) else None
    except (ValueError, TypeError, AttributeError):
        raise CommerceError("refund_hold_unknown", "Account changes must be checked before a refund can proceed.", 409)


def checked_customer(user_id):
    from .app import _customers
    client = _customers()
    if client is None:
        raise CommerceError("auth_unavailable", "Customer details cannot be verified for this refund.", 503)
    identity = client.get_user(user_id)
    if not identity.get("authenticated") or identity.get("user") != user_id or "refund_hold_until" not in identity:
        raise CommerceError("auth_unavailable", "Customer refund protections are unavailable.", 503)
    if identity.get("requires_password_change"):
        raise CommerceError("password_change_required", "The customer must change their temporary password before a refund can proceed.", 409)
    return identity


@bp.post("/api/billing/refunds/<refund_id>/issue")
@customer_route(admin=True)
def issue_refund(identity, refund_id):
    """Approve a full refund to the original card, never to replacement details.

    Before calling the provider, a durable submitting state is committed. An
    uncertain external outcome is reconciled from a signed callback, never by
    automatically sending a second refund. Debits/compensation are idempotent.
    """
    config = _gateway_configuration()
    if os.getenv("BILLING_CARDSTREAM_REFUNDS_ENABLED") != "1" or not config or not config["secret"] or not config["merchant"]:
        raise CommerceError("refunds_unavailable", "Automatic card refunds have not been enabled.", 503)
    if body().get("confirm") is not True:
        raise CommerceError("confirmation_required", "Confirm the full refund to the original card.")
    from .app import _require_chain
    features = set(_require_chain().health().get("features") or [])
    if not {"atomic_cashout_v1", "idempotent_entry_v1"} <= features:
        raise CommerceError("ledger_upgrade_required", "The ledger must be upgraded before automatic refunds can be enabled.", 503)
    with _engine().begin() as conn:
        refund = conn.execute(select(refunds).where(refunds.c.id == refund_id).with_for_update()).mappings().first()
        if refund is None:
            raise CommerceError("refund_not_found", "Refund request not found.", 404)
        if refund["status"] in {"submitting", "reconciliation_required", "refunded", "refund_failed", "manual_review", "restore_pending"}:
            return jsonify(status=refund["status"]), 200
        if refund["status"] not in {"requested", "under_review", "debit_pending", "debited"}:
            raise CommerceError("refund_not_approved", "This request cannot enter settlement.", 409)
        customer = checked_customer(refund["user_id"])
        hold = refund_hold(customer)
        if hold:
            conn.execute(update(refunds).where(refunds.c.id == refund_id).values(hold_until=hold, updated_at=now()))
            return jsonify(error="refund_on_hold", details="Customer or payment details changed recently. Refunds are held until the cooling-off period ends.", hold_until=hold), 409
        order = conn.execute(select(orders).where(orders.c.id == refund["order_id"])).mappings().one()
        if order["status"] != "paid":
            raise CommerceError("payment_not_confirmed", "The original payment is not confirmed.", 409)
        status = refund["status"]
        if status != "debited":
            # Save intent before contacting the ledger, so interrupted attempts
            # resume with the same event identity and recover the original result.
            conn.execute(update(refunds).where(refunds.c.id == refund_id).values(status="debit_pending", reviewed_by=identity["user"], hold_until=None, updated_at=now()))

    if status != "debited":
        result = _require_chain().apply_token("user", refund["user_id"], entry_type="cashout", delta=-order["tokens"],
                    request_id=f"cardstream-refund-debit:{refund_id}",
                    meta={"require_full_amount": True, "order_id": order["id"], "refund_id": refund_id, "source": "card_refund"})
        entry = result.get("entry") or {}
        # Older ledgers cannot safely support automatic refunds. Missing original
        # retry outcomes or partial debits must be reconciled by an operator.
        complete = entry.get("delta") == -order["tokens"] and not entry.get("shortfall")
        with _engine().begin() as conn:
            conn.execute(update(refunds).where(refunds.c.id == refund_id, refunds.c.status == "debit_pending").values(
                status="debited" if complete else "manual_review", updated_at=now(),
                response="" if complete else "This request needs an account review before it can proceed."))
        if not complete:
            return jsonify(status="manual_review", details="No card refund was sent. Check available paid tokens and reconcile any ledger debit."), 409

    with _engine().begin() as conn:
        refund = conn.execute(select(refunds).where(refunds.c.id == refund_id).with_for_update()).mappings().one()
        if refund["status"] != "debited":
            return jsonify(status=refund["status"])
        # Re-check after the ledger step: a recent details change must hold even
        # a previously approved or interrupted refund.
        hold = refund_hold(checked_customer(refund["user_id"]))
        if hold:
            conn.execute(update(refunds).where(refunds.c.id == refund_id).values(hold_until=hold, updated_at=now()))
            return jsonify(error="refund_on_hold", details="Refund held after a recent details change.", hold_until=hold), 409
        conn.execute(update(refunds).where(refunds.c.id == refund_id).values(status="submitting", hold_until=None, updated_at=now()))
    fields = dict(merchantID=config["merchant"], action="REFUND", type="1", xref=order["payment_reference"],
                  amount=str(order["amount_minor"]), currency="826", orderRef=refund_id, transactionUnique=refund_id,
                  callbackURL=f"{config['api']}/api/billing/cardstream/callback")
    fields["signature"] = sign(fields, config["secret"])
    try:
        response = requests.post("https://gateway.cardstream.com/direct/", data=fields, timeout=20, allow_redirects=False)
        response.raise_for_status()
        pairs = parse_qsl(response.text, keep_blank_values=True)
        if len({key for key, _ in pairs}) != len(pairs):
            raise ValueError("duplicate fields")
        signed = verified_fields(dict(pairs), config["secret"])
        if not signed or signed.get("merchantID") != config["merchant"] or signed.get("action") != "REFUND" or signed.get("orderRef") != refund_id:
            raise ValueError("unverified response")
        settle_refund_response(signed)
    except (requests.RequestException, ValueError, CommerceError):
        with _engine().begin() as conn:
            conn.execute(update(refunds).where(refunds.c.id == refund_id, refunds.c.status == "submitting").values(status="reconciliation_required", updated_at=now()))
    with _engine().connect() as conn:
        status = conn.execute(select(refunds.c.status).where(refunds.c.id == refund_id)).scalar_one()
    return jsonify(status=status)


def settle_refund_response(fields):
    from .app import _require_chain
    with _engine().begin() as conn:
        refund = conn.execute(select(refunds).where(refunds.c.id == fields["orderRef"]).with_for_update()).mappings().first()
        if refund is None:
            raise CommerceError("refund_not_found", "Refund request not found.", 404)
        order = conn.execute(select(orders).where(orders.c.id == refund["order_id"])).mappings().one()
        if fields["amount"] != str(order["amount_minor"]) or fields["currency"] != "826" or not fields["xref"] or len(fields["xref"]) > 128:
            raise CommerceError("refund_mismatch", "Refund does not match the original purchase.", 409)
        if refund["refund_reference"] and refund["refund_reference"] != fields["xref"]:
            raise CommerceError("refund_mismatch", "Refund reference changed.", 409)
        if refund["status"] == "refunded":
            return
        if refund["status"] == "refund_failed" and fields["responseCode"] != "0":
            return
        if refund["status"] not in {"submitting", "reconciliation_required", "restore_pending"}:
            raise CommerceError("unexpected_refund", "Refund requires reconciliation.", 409)
        if fields["responseCode"] == "0":
            if refund["status"] == "restore_pending":
                raise CommerceError("conflicting_refund", "Conflicting provider outcomes require reconciliation.", 409)
            conn.execute(update(refunds).where(refunds.c.id == refund["id"]).values(status="refunded", refund_reference=fields["xref"],
                         response="Your refund has been accepted by the payment provider for the original card. Your bank determines when it appears.", updated_at=now()))
            return
        conn.execute(update(refunds).where(refunds.c.id == refund["id"]).values(status="restore_pending", refund_reference=fields["xref"], updated_at=now()))
    # Commit the provider's failure before compensating, so successful credit plus
    # lost acknowledgement can be retried without sending money a second time.
    _require_chain().apply_token("user", refund["user_id"], entry_type="refund", delta=order["tokens"],
                 request_id=f"cardstream-refund-restore:{refund['id']}", meta={"source": "failed_card_refund", "refund_id": refund["id"]})
    with _engine().begin() as conn:
        conn.execute(update(refunds).where(refunds.c.id == refund["id"], refunds.c.status == "restore_pending").values(status="refund_failed",
            response="The payment provider could not complete the refund. Your tokens have been restored. Please contact us for help.", updated_at=now()))


def register_commerce(app):
    database_url = os.getenv("BILLING_DATABASE_URL", "").strip()
    if database_url:
        # Production commerce records live in Continuum PostgreSQL, never a pod file.
        if not database_url.startswith("postgresql+psycopg://"):
            raise ValueError("BILLING_DATABASE_URL must use postgresql+psycopg://")
        app.extensions["commerce_engine"] = create_engine(database_url, pool_pre_ping=True)
    app.register_blueprint(bp)

    @app.cli.command("init-commerce-db")
    def init_commerce_db():
        """Create the initial commerce schema in the configured PostgreSQL database."""
        metadata.create_all(_engine())
        click.echo("Commerce schema ready.")

    register_error_handlers(app)


def register_error_handlers(app):
    # Blueprint handlers prevent provider or database details leaking to customers.
    for error_type in (CommerceError, CustomersClientError, NmChainError, SQLAlchemyError):
        def handle(error):
            if isinstance(error, CommerceError):
                return jsonify(error=error.code, details=error.message), error.status
            app.logger.warning("Commerce dependency failure (%s)", type(error).__name__)
            return jsonify(error="billing_unavailable", details="We could not confirm the result. Refresh before trying again."), 503
        app.register_error_handler(error_type, handle)

    @app.after_request
    def private_commerce(response):
        if request.path.startswith("/api/billing/"):
            response.headers["Cache-Control"] = "no-store"
        return response
