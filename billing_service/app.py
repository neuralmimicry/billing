from __future__ import annotations

import logging
import os
import secrets
import uuid
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlencode

from flask import Flask, Response, jsonify, make_response, redirect, render_template, request, send_from_directory

from .config import Settings
from .customers_client import CustomersClient, CustomersClientError
from .dashboard_analytics import build_admin_dashboard, build_customer_dashboard, extract_observed_accounts
from .nmchain_client import NmChainClient, NmChainError

logger = logging.getLogger(__name__)


def _settings() -> Settings:
    from flask import current_app

    return current_app.extensions["nm_settings"]


def _chain() -> Optional[NmChainClient]:
    from flask import current_app

    return current_app.extensions.get("nm_chain")


def _customers() -> Optional[CustomersClient]:
    from flask import current_app

    return current_app.extensions.get("customers_client")


def _extract_bearer_token(auth_header: Optional[str]) -> Optional[str]:
    if not auth_header:
        return None
    parts = auth_header.strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return auth_header.strip()


def _match_app_token() -> Optional[str]:
    token = _extract_bearer_token(request.headers.get("Authorization") or request.headers.get("authorization"))
    if not token:
        return None
    for app_id, value in _settings().app_tokens.items():
        if secrets.compare_digest(token, value):
            return app_id
    return None


def require_app_token(view: Callable[..., Response]) -> Callable[..., Response]:
    def wrapper(*args: Any, **kwargs: Any) -> Response:
        actor = _match_app_token()
        if not actor:
            return jsonify({"error": "forbidden"}), 403
        request.environ["nm.app_actor"] = actor
        return view(*args, **kwargs)

    wrapper.__name__ = view.__name__
    return wrapper


def _allowed_origin() -> Optional[str]:
    configured = os.getenv("BILLING_CORS_ORIGINS") or os.getenv("NEURALMIMICRY_SITE_BASE") or "https://neuralmimicry.ai"
    allowed = {item.strip().rstrip("/") for item in configured.split(",") if item.strip()}
    origin = (request.headers.get("Origin") or "").rstrip("/")
    return origin if origin and origin in allowed else None


def _apply_cors(response: Response) -> Response:
    allowed = _allowed_origin()
    if allowed:
        response.headers["Access-Control-Allow-Origin"] = allowed
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
    return response


def _require_chain() -> NmChainClient:
    chain = _chain()
    if not chain:
        raise NmChainError("nmchain_not_configured")
    return chain


def _identity_from_request() -> Optional[Dict[str, Any]]:
    if _settings().auth_open:
        user = (request.headers.get("X-Debug-User") or "developer").strip()
        role = (request.headers.get("X-Debug-Role") or "admin").strip() or "admin"
        groups = _coerce_identity_groups(request.headers.get("X-Debug-Groups"))
        active_team = _normalize_active_team(request.headers.get("X-Debug-Active-Team"))
        return _normalize_identity(
            {
                "authenticated": True,
                "user": user,
                "role": role,
                "groups": groups,
                "active_team": active_team,
            }
        )
    client = _customers()
    if not client:
        if _settings().require_customers:
            raise CustomersClientError("customers_not_configured")
        return None
    payload = client.resolve_session(
        authorization=request.headers.get("Authorization") or request.headers.get("authorization"),
        cookie_header=request.headers.get("Cookie"),
    )
    if payload.get("authenticated"):
        return _normalize_identity(payload)
    return None


def _verify_password(username: str, password: str) -> bool:
    client = _customers()
    if not client:
        return _settings().auth_open
    payload = client.verify_credentials(username, password)
    return bool(payload.get("authenticated"))


def _coerce_identity_groups(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_values = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_values = [str(item).strip() for item in value if str(item).strip()]
    else:
        raw_values = []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_values:
        lowered = item.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(lowered)
    return normalized


def _coerce_optional_int(value: Any, default: int = 0) -> int:
    if value in (None, ""):
        return default
    try:
        return int(value)
    except Exception:
        return default


def _normalize_active_team(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        payload = dict(value)
        team_id = str(payload.get("team_id") or payload.get("id") or "").strip()
        if not team_id:
            return None
        payload["team_id"] = team_id
        return payload
    if isinstance(value, str):
        team_id = value.strip()
        if team_id:
            return {"team_id": team_id}
    return None


def _normalize_identity(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    user = str(payload.get("user") or "").strip()
    if not user:
        return None
    role = str(payload.get("role") or "user").strip().lower() or "user"
    groups = _coerce_identity_groups(payload.get("groups"))
    if role and role not in groups:
        groups.insert(0, role)
    active_team = _normalize_active_team(payload.get("active_team"))
    return {
        **payload,
        "authenticated": bool(payload.get("authenticated")),
        "user": user,
        "role": role,
        "groups": groups,
        "active_team": active_team,
        "team_count": _coerce_optional_int(payload.get("team_count"), 1 if active_team else 0),
        "pending_invitation_count": _coerce_optional_int(payload.get("pending_invitation_count"), 0),
        "is_admin": role == "admin" or "admin" in groups,
    }


def _active_team_label(identity: Optional[Dict[str, Any]]) -> Optional[str]:
    active_team = identity.get("active_team") if isinstance(identity, dict) else None
    if not isinstance(active_team, dict):
        return None
    return (
        str(active_team.get("team_name") or "").strip()
        or str(active_team.get("name") or "").strip()
        or str(active_team.get("team_id") or "").strip()
        or None
    )


def _normalize_snapshot(snapshot: Dict[str, Any], *, include_btc_rate: bool = False) -> Dict[str, Any]:
    balance = int(snapshot.get("balance") or snapshot.get("tokens") or 0)
    paid_balance = int(snapshot.get("paid_balance") or balance)
    free_balance = int(snapshot.get("free_balance") or 0)
    reserved = int(snapshot.get("reserved") or 0)
    available = int(snapshot.get("available") or max(0, balance - reserved))
    in_use = int(snapshot.get("in_use") or reserved)
    capacity = int(snapshot.get("capacity") or snapshot.get("last_topup_tokens") or balance)
    display_capacity = int(snapshot.get("display_capacity") or max(1, capacity, balance))
    low_threshold = int(snapshot.get("low_threshold") or (round(capacity * 0.2) if capacity else 0))
    payload = {
        "balance": balance,
        "tokens": balance,
        "paid_balance": paid_balance,
        "free_balance": free_balance,
        "available": available,
        "reserved": reserved,
        "in_use": in_use,
        "last_topup_tokens": int(snapshot.get("last_topup_tokens") or capacity or 0),
        "capacity": capacity,
        "display_capacity": display_capacity,
        "low_threshold": low_threshold,
        "status": snapshot.get("status") or ("low" if capacity and balance <= low_threshold else "ok"),
        "last_topup_at": snapshot.get("last_topup_at"),
        "updated_at": snapshot.get("updated_at"),
        "spent_total": int(snapshot.get("spent_total") or 0),
        "cashout_total": int(snapshot.get("cashout_total") or 0),
        "shortfall_total": int(snapshot.get("shortfall_total") or 0),
        "free_grant_total": int(snapshot.get("free_grant_total") or 0),
        "identity": snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else None,
        "scope": snapshot.get("scope") or "personal",
    }
    if include_btc_rate:
        payload["btc_rate"] = _settings().btc_rate
    return payload


def _user_snapshot(user: str) -> Dict[str, Any]:
    chain = _require_chain()
    snapshot = chain.account_snapshot("user", user)
    return _normalize_snapshot(snapshot, include_btc_rate=True)


def _account_snapshot(scope: str, account_id: str) -> Dict[str, Any]:
    chain = _require_chain()
    snapshot = chain.account_snapshot(scope, account_id)
    return _normalize_snapshot(snapshot, include_btc_rate=(scope == "user"))


def _ledger_entries(scope: str, account_id: str, limit: int) -> Dict[str, Any]:
    chain = _require_chain()
    return chain.ledger_entries(scope, account_id, limit=limit)


def _request_path_with_query() -> str:
    path = request.full_path if request.query_string else request.path
    return path[:-1] if path.endswith("?") else path


def _login_redirect() -> Response:
    return redirect(f"/login?{urlencode({'next': _request_path_with_query()})}", code=302)


def _api_origin_hint() -> str:
    host = (request.headers.get("X-Forwarded-Host") or request.host or "").strip()
    if host == "api.neuralmimicry.ai":
        return "api.neuralmimicry.ai via vega -> spirit"
    return host or "billing.internal"


def _dashboard_bootstrap(kind: str, *, identity: Dict[str, Any]) -> Dict[str, Any]:
    groups = identity.get("groups") if isinstance(identity.get("groups"), list) else []
    return {
        "kind": kind,
        "identity": {
            "user": identity.get("user"),
            "role": identity.get("role"),
            "groups": groups,
            "active_team": identity.get("active_team"),
            "team_count": identity.get("team_count"),
            "pending_invitation_count": identity.get("pending_invitation_count"),
        },
        "can_switch_dashboard": bool(identity.get("is_admin")),
        "endpoints": {
            "data": "/api/billing/dashboard/customer" if kind == "customer" else "/api/billing/dashboard/admin",
            "customer": "/billing",
            "admin": "/billing/admin",
        },
        "export_prefix": f"neuralmimicry-billing-{kind}",
    }


def _customer_dashboard_payload(user: str) -> Dict[str, Any]:
    settings = _settings()
    snapshot = _user_snapshot(user)
    ledger_payload = _ledger_entries("user", user, settings.dashboard_customer_ledger_limit)
    entries = ledger_payload.get("entries") if isinstance(ledger_payload.get("entries"), list) else []
    return build_customer_dashboard(
        snapshot,
        entries,
        btc_rate=settings.btc_rate,
        anomaly_config=settings.anomaly_config(),
    )


def _admin_dashboard_payload() -> Dict[str, Any]:
    settings = _settings()
    chain = _require_chain()
    chain_status = chain.chain_status()
    blocks_payload = chain.list_blocks(limit=settings.dashboard_admin_block_limit)
    blocks = blocks_payload.get("blocks") if isinstance(blocks_payload.get("blocks"), list) else []
    observed_accounts = extract_observed_accounts(blocks)
    snapshots_by_account: Dict[str, Dict[str, Any]] = {}
    for account in observed_accounts[: settings.dashboard_admin_account_limit]:
        scope = str(account.get("scope") or "user").strip() or "user"
        account_id = str(account.get("account_id") or "").strip()
        account_ref = str(account.get("account_ref") or f"{scope}/{account_id}").strip()
        if not account_id:
            continue
        try:
            snapshots_by_account[account_ref] = _normalize_snapshot(chain.account_snapshot(scope, account_id))
        except NmChainError as exc:
            logger.warning("admin snapshot failed for %s: %s", account_ref, exc)
    return build_admin_dashboard(
        chain_status,
        blocks,
        snapshots_by_account,
        btc_rate=settings.btc_rate,
        anomaly_config=settings.anomaly_config(),
    )


def create_app() -> Flask:
    settings = Settings.from_env()
    app = Flask(__name__)
    app.extensions["nm_settings"] = settings
    app.extensions["nm_chain"] = NmChainClient.from_env()
    app.extensions["customers_client"] = CustomersClient.from_env()

    @app.before_request
    def _before_request() -> Optional[Response]:
        if request.method == "OPTIONS":
            return make_response("", 204)
        return None

    @app.after_request
    def _after_request(response: Response) -> Response:
        return _apply_cors(response)

    @app.route("/billing/assets/<path:filename>")
    def billing_dashboard_asset(filename: str) -> Response:
        return send_from_directory(app.static_folder, filename, max_age=settings.dashboard_asset_cache_seconds)

    @app.route("/billing")
    def billing_dashboard_customer() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed for customer dashboard: %s", exc)
            return make_response("Authentication backend unavailable.", 503)
        if not identity or not identity.get("user"):
            return _login_redirect()
        return make_response(
            render_template(
                "dashboard.html",
                dashboard_title="NeuralMimicry Billing Intelligence",
                dashboard_kind="customer",
                dashboard_bootstrap=_dashboard_bootstrap("customer", identity=identity),
                dashboard_copy="Watch balances, settlement routes, token flow, and anomaly posture without leaving NeuralMimicry's billing boundary.",
                dashboard_kicker="Customer dashboard",
                dashboard_heading="Billing envelope and settlement activity",
                current_user=str(identity.get("user") or "").strip(),
                user_role=str(identity.get("role") or "user"),
                user_groups=identity.get("groups") or [],
                active_team_label=_active_team_label(identity),
                can_switch_dashboard=bool(identity.get("is_admin")),
                api_origin_hint=_api_origin_hint(),
            )
        )

    @app.route("/billing/admin")
    def billing_dashboard_admin() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed for admin dashboard: %s", exc)
            return make_response("Authentication backend unavailable.", 503)
        if not identity or not identity.get("user"):
            return _login_redirect()
        if not bool(identity.get("is_admin")):
            return make_response("Admin role required.", 403)
        return make_response(
            render_template(
                "dashboard.html",
                dashboard_title="NeuralMimicry Billing Control Plane",
                dashboard_kind="admin",
                dashboard_bootstrap=_dashboard_bootstrap("admin", identity=identity),
                dashboard_copy="Track portfolio movement, provider concentration, and the anomaly review queue from a chain-backed operator surface.",
                dashboard_kicker="Admin dashboard",
                dashboard_heading="Portfolio control and billing anomaly queue",
                current_user=str(identity.get("user") or "").strip(),
                user_role=str(identity.get("role") or "user"),
                user_groups=identity.get("groups") or [],
                active_team_label=_active_team_label(identity),
                can_switch_dashboard=True,
                api_origin_hint=_api_origin_hint(),
            )
        )

    @app.route("/api/health")
    def api_health() -> Response:
        return jsonify(
            {
                "status": "ok",
                "service": settings.service_name,
                "version": settings.version,
                "nmchain_enabled": bool(_chain()),
                "customers_enabled": bool(_customers()),
                "auth_open": settings.auth_open,
                "app_tokens_configured": sorted(settings.app_tokens.keys()),
            }
        )

    @app.route("/api/version")
    def api_version() -> Response:
        return jsonify({"service": settings.service_name, "version": settings.version})

    @app.route("/api/billing/dashboard/customer")
    def api_billing_dashboard_customer() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed for customer dashboard api: %s", exc)
            return jsonify({"error": "auth_unavailable"}), 503
        if not identity or not identity.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        try:
            payload = _customer_dashboard_payload(str(identity.get("user") or "").strip())
        except NmChainError as exc:
            logger.warning("customer dashboard lookup failed: %s", exc)
            return jsonify({"error": str(exc)}), 503
        return jsonify(payload)

    @app.route("/api/billing/dashboard/admin")
    def api_billing_dashboard_admin() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed for admin dashboard api: %s", exc)
            return jsonify({"error": "auth_unavailable"}), 503
        if not identity or not identity.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        if not bool(identity.get("is_admin")):
            return jsonify({"error": "forbidden"}), 403
        try:
            payload = _admin_dashboard_payload()
        except NmChainError as exc:
            logger.warning("admin dashboard lookup failed: %s", exc)
            return jsonify({"error": str(exc)}), 503
        return jsonify(payload)

    @app.route("/api/tokens", methods=["GET", "POST"])
    def api_tokens() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed: %s", exc)
            return jsonify({"error": "auth_unavailable"}), 503
        if not identity or not identity.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        user = str(identity.get("user") or "").strip()
        try:
            snapshot = _user_snapshot(user)
        except NmChainError as exc:
            logger.warning("nmchain snapshot failed: %s", exc)
            return jsonify({"error": str(exc)}), 503

        if request.method == "GET":
            return jsonify(snapshot)

        payload = request.get_json(force=True, silent=True) or {}
        action = str(payload.get("action") or "review").strip().lower()
        username = str(payload.get("username") or user).strip()
        if action != "grant" and username and username != user:
            return jsonify({"error": "invalid_user", "details": "Username mismatch."}), 403
        if action == "review":
            return jsonify(snapshot)

        if action in {"add", "cashout", "grant"}:
            password = str(payload.get("password") or "")
            if not password or not _verify_password(user, password):
                return jsonify({"error": "invalid_credentials", "details": "Password verification failed."}), 401

        chain = _require_chain()

        if action == "add":
            tokens_raw = payload.get("token_amount")
            btc_raw = payload.get("btc_amount") or payload.get("btc_value")
            tokens = 0
            btc_value = None
            if tokens_raw not in (None, ""):
                try:
                    tokens = int(float(tokens_raw))
                except Exception:
                    tokens = 0
            elif btc_raw not in (None, ""):
                try:
                    btc_value = float(btc_raw)
                    tokens = int(round(btc_value / settings.btc_rate))
                except Exception:
                    tokens = 0
            if tokens <= 0:
                return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
            meta = {
                "tokens": tokens,
                "btc_amount": btc_value,
                "btc_rate": settings.btc_rate,
                "btc_txid": payload.get("btc_txid"),
                "btc_address": payload.get("btc_address"),
                "source": payload.get("source") or "portal",
                "currency": payload.get("settlement_currency") or "GBP",
                "payment_provider": payload.get("payment_provider"),
                "payment_channel": payload.get("payment_channel"),
                "payment_method": payload.get("payment_method"),
                "checkout_flow": payload.get("checkout_flow"),
                "payment_id": payload.get("payment_id") or payload.get("checkout_id"),
            }
            request_id = str(meta.get("payment_id") or meta.get("btc_txid") or "").strip() or f"billing-topup:{user}:{uuid.uuid4().hex}"
            try:
                chain.capture_payment(
                    user,
                    tokens=tokens,
                    amount_minor=_safe_int(payload.get("amount_minor")),
                    currency=str(meta.get("currency") or "").strip() or None,
                    provider=str(meta.get("payment_provider") or "").strip() or None,
                    payment_id=str(meta.get("payment_id") or "").strip() or None,
                    checkout_flow=str(meta.get("checkout_flow") or "").strip() or None,
                    request_id=request_id,
                    meta=meta,
                )
            except NmChainError as exc:
                logger.warning("payment capture failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            return jsonify({"message": "Tokens added.", **_user_snapshot(user)})

        if action == "cashout":
            try:
                tokens = int(float(payload.get("token_amount") or 0))
            except Exception:
                tokens = 0
            if tokens <= 0:
                return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
            if tokens > snapshot.get("paid_balance", snapshot.get("balance", 0)):
                return jsonify({"error": "insufficient_tokens", "details": "Not enough tokens to cash out."}), 409
            meta = {
                "tokens": tokens,
                "btc_address": payload.get("btc_address"),
                "source": payload.get("source") or "portal",
            }
            try:
                chain.apply_token(
                    "user",
                    user,
                    entry_type="cashout",
                    delta=-tokens,
                    request_id=str(payload.get("payout_reference") or payload.get("settlement_reference") or "").strip() or f"billing-cashout:{user}:{uuid.uuid4().hex}",
                    meta=meta,
                )
            except NmChainError as exc:
                logger.warning("cashout failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            return jsonify({"message": "Cashout recorded.", **_user_snapshot(user)})

        if action == "grant":
            if not bool(identity.get("is_admin")):
                return jsonify({"error": "forbidden", "details": "Admin role required."}), 403
            target_user = str(payload.get("target_user") or payload.get("recipient") or payload.get("username") or "").strip()
            if not target_user:
                return jsonify({"error": "target_required", "details": "Target user is required."}), 400
            try:
                tokens = int(float(payload.get("token_amount") or 0))
            except Exception:
                tokens = 0
            if tokens <= 0:
                return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
            meta = {
                "tokens": tokens,
                "granted_by": user,
                "note": payload.get("note") or payload.get("reason"),
                "source": payload.get("source") or "admin",
            }
            try:
                chain.apply_token(
                    "user",
                    target_user,
                    entry_type="grant",
                    delta=tokens,
                    request_id=f"billing-grant:{target_user}:{uuid.uuid4().hex}",
                    meta=meta,
                )
            except NmChainError as exc:
                logger.warning("grant failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            return jsonify({"message": "Free tokens granted.", "target": target_user, **_user_snapshot(target_user)})

        if action == "debit":
            try:
                tokens = int(float(payload.get("token_amount") or 0))
            except Exception:
                tokens = 0
            if tokens <= 0:
                return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
            meta = {
                "tokens": tokens,
                "source": payload.get("source") or "api",
                "note": payload.get("note") or payload.get("reason"),
                "operation": payload.get("operation"),
                "workspace_id": payload.get("workspace_id"),
            }
            request_id = str(payload.get("request_id") or payload.get("operation_id") or "").strip() or f"billing-debit:{user}:{uuid.uuid4().hex}"
            try:
                result = chain.apply_token("user", user, entry_type="debit", delta=-tokens, request_id=request_id, meta=meta)
            except NmChainError as exc:
                logger.warning("debit failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            entry = result.get("entry") if isinstance(result.get("entry"), dict) else {}
            shortfall = int(entry.get("shortfall") or 0)
            used_total = abs(int(entry.get("delta") or 0))
            if shortfall > 0 or used_total < tokens:
                return jsonify(
                    {
                        "error": "insufficient_tokens",
                        "details": "Not enough tokens to debit.",
                        "requested": tokens,
                        "used": used_total,
                        "shortfall": max(shortfall, tokens - used_total),
                        **_user_snapshot(user),
                    }
                ), 409
            return jsonify({"message": "Tokens debited.", "entry": entry, **_user_snapshot(user)})

        if action == "refund":
            try:
                tokens = int(float(payload.get("token_amount") or 0))
            except Exception:
                tokens = 0
            if tokens <= 0:
                return jsonify({"error": "invalid_amount", "details": "Token amount must be positive."}), 400
            meta = {
                "tokens": tokens,
                "source": payload.get("source") or "api",
                "note": payload.get("note") or payload.get("reason"),
                "operation": payload.get("operation"),
                "workspace_id": payload.get("workspace_id"),
            }
            request_id = str(payload.get("request_id") or payload.get("operation_id") or "").strip() or f"billing-refund:{user}:{uuid.uuid4().hex}"
            try:
                result = chain.apply_token("user", user, entry_type="refund", delta=tokens, request_id=request_id, meta=meta)
            except NmChainError as exc:
                logger.warning("refund failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            return jsonify({"message": "Tokens refunded.", "entry": result.get("entry"), **_user_snapshot(user)})

        if action == "sync":
            target = payload.get("balance")
            if target is None:
                return jsonify({"error": "balance_required"}), 400
            try:
                target_balance = int(float(target))
            except Exception:
                return jsonify({"error": "invalid_balance"}), 400
            if target_balance < 0:
                return jsonify({"error": "invalid_balance"}), 400
            capacity = payload.get("capacity") or payload.get("last_topup_tokens")
            try:
                capacity_val = int(float(capacity)) if capacity not in (None, "") else None
            except Exception:
                capacity_val = None
            target_paid = payload.get("paid_balance")
            target_free = payload.get("free_balance")
            try:
                target_paid_val = int(float(target_paid)) if target_paid not in (None, "") else None
            except Exception:
                target_paid_val = None
            try:
                target_free_val = int(float(target_free)) if target_free not in (None, "") else None
            except Exception:
                target_free_val = None
            delta = target_balance - int(snapshot.get("balance") or 0)
            try:
                chain.apply_token(
                    "user",
                    user,
                    entry_type="sync",
                    delta=delta,
                    meta={
                        "target_balance": target_balance,
                        "capacity": capacity_val,
                        "source": payload.get("source") or "portal",
                        "sync_user": payload.get("user") or user,
                        "sync_role": payload.get("role"),
                        "target_paid_balance": target_paid_val,
                        "target_free_balance": target_free_val,
                    },
                )
            except NmChainError as exc:
                logger.warning("sync failed: %s", exc)
                return jsonify({"error": str(exc)}), 503
            status = "matched" if delta == 0 else "adjusted"
            return jsonify({"message": "Sync complete.", "status": status, **_user_snapshot(user)})

        return jsonify({"error": "invalid_action"}), 400

    @app.route("/api/tokens/ledger")
    def api_tokens_ledger() -> Response:
        try:
            identity = _identity_from_request()
        except CustomersClientError as exc:
            logger.warning("customers lookup failed: %s", exc)
            return jsonify({"error": "auth_unavailable"}), 503
        if not identity or not identity.get("user"):
            return jsonify({"error": "unauthorized"}), 401
        target = str(request.args.get("user") or "").strip()
        user = str(identity.get("user") or "").strip()
        if target:
            if not bool(identity.get("is_admin")):
                return jsonify({"error": "forbidden"}), 403
            user = target
        try:
            limit = int(request.args.get("limit") or 50)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 500))
        try:
            payload = _ledger_entries("user", user, limit)
        except NmChainError as exc:
            logger.warning("ledger lookup failed: %s", exc)
            return jsonify({"error": str(exc)}), 503
        return jsonify({"entries": payload.get("entries") if isinstance(payload, dict) else []})

    @app.route("/api/internal/accounts/<scope>/<account_id>")
    @require_app_token
    def api_internal_account_snapshot(scope: str, account_id: str) -> Response:
        try:
            return jsonify(_account_snapshot(scope, account_id))
        except NmChainError as exc:
            logger.warning("internal snapshot failed: %s", exc)
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/internal/accounts/<scope>/<account_id>/ledger")
    @require_app_token
    def api_internal_account_ledger(scope: str, account_id: str) -> Response:
        try:
            limit = int(request.args.get("limit") or 50)
        except Exception:
            limit = 50
        limit = max(1, min(limit, 500))
        try:
            return jsonify(_ledger_entries(scope, account_id, limit))
        except NmChainError as exc:
            logger.warning("internal ledger failed: %s", exc)
            return jsonify({"error": str(exc)}), 503

    @app.route("/api/internal/accounts/<scope>/<account_id>/events", methods=["POST"])
    @require_app_token
    def api_internal_account_event(scope: str, account_id: str) -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        entry_type = str(payload.get("entry_type") or payload.get("type") or "").strip().lower()
        if not entry_type:
            return jsonify({"error": "entry_type_required"}), 400
        try:
            delta = int(payload.get("delta") or 0)
        except Exception:
            return jsonify({"error": "invalid_delta"}), 400
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        request_id = str(payload.get("request_id") or "").strip() or None
        try:
            result = _require_chain().apply_token(scope, account_id, entry_type=entry_type, delta=delta, request_id=request_id, meta=meta)
        except NmChainError as exc:
            logger.warning("internal token event failed: %s", exc)
            return jsonify({"error": str(exc)}), 503
        return jsonify(result)

    @app.route("/api/internal/payments", methods=["POST"])
    @require_app_token
    def api_internal_payment() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        user_id = str(payload.get("user_id") or payload.get("user") or "").strip()
        try:
            tokens = int(payload.get("tokens") or 0)
        except Exception:
            tokens = 0
        if not user_id or tokens <= 0:
            return jsonify({"error": "user_id_and_positive_tokens_required"}), 400
        try:
            result = _require_chain().capture_payment(
                user_id,
                tokens=tokens,
                amount_minor=_safe_int(payload.get("amount_minor")),
                currency=str(payload.get("currency") or "").strip() or None,
                provider=str(payload.get("provider") or "").strip() or None,
                payment_id=str(payload.get("payment_id") or "").strip() or None,
                checkout_flow=str(payload.get("checkout_flow") or "").strip() or None,
                request_id=str(payload.get("request_id") or "").strip() or None,
                meta=payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
            )
        except NmChainError as exc:
            logger.warning("internal payment failed: %s", exc)
            return jsonify({"error": str(exc)}), 503
        return jsonify(result)

    return app


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def main() -> None:
    logging.basicConfig(
        level=os.getenv("BILLING_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app()
    settings = app.extensions["nm_settings"]
    app.run(host=settings.host, port=settings.port)
