from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine

from billing_service.app import create_app
from billing_service.commerce import metadata


def _new_budget_app(monkeypatch, tmp_path):
    monkeypatch.delenv("BILLING_DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "BILLING_APP_TOKENS",
        "refiner=refiner-test-token,conductor=conductor-test-token,continuum=continuum-test-token",
    )
    monkeypatch.setenv("BILLING_AUTH_OPEN", "1")
    monkeypatch.setenv("BILLING_REQUIRE_CUSTOMERS", "0")
    app = create_app()
    app.config["TESTING"] = True
    engine = create_engine(f"sqlite:///{tmp_path / 'billing-budgets.sqlite'}")
    metadata.create_all(engine)
    app.extensions["commerce_engine"] = engine
    return app, app.test_client()


def _admin_headers():
    return {"X-Debug-User": "operator", "X-Debug-Role": "admin"}


def _service_headers(service: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {service}-test-token"}


def _create_budget(client, *, ceiling=1000, providers=None, key="maintenance-1"):
    now = datetime.now(timezone.utc)
    response = client.post(
        "/api/billing/admin/infrastructure-budgets",
        headers=_admin_headers(),
        json={
            "idempotency_key": key,
            "name": "Centriq maintenance capacity",
            "currency": "USD",
            "ceiling_minor": ceiling,
            "providers": providers or ["aws"],
            "starts_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
        },
    )
    assert response.status_code == 201, response.get_json()
    return response.get_json()["budget"]


def _hold_payload(budget_id: str, *, request_id="maintenance-1/compute", amount=700, provider="aws"):
    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    return {
        "budget_id": budget_id,
        "request_id": request_id,
        "provider": provider,
        "currency": "USD",
        "maximum_cost_minor": amount,
        "expires_at": expires.isoformat(),
        "maintenance_proposal_id": "maintenance-1",
    }


def test_billing_admin_approves_budget_and_conductor_reserves_atomically(monkeypatch, tmp_path):
    _app, client = _new_budget_app(monkeypatch, tmp_path)
    budget = _create_budget(client)
    assert budget["status"] == "approved"
    assert budget["approved_by"] == "operator"
    assert budget["ceiling"] == {"currency": "USD", "minor_units": 1000}

    payload = _hold_payload(budget["budget_id"])
    first = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=payload,
    )
    assert first.status_code == 201, first.get_json()
    hold = first.get_json()["hold"]
    assert hold["status"] == "reserved"
    assert hold["maximum_cost"] == {"currency": "USD", "minor_units": 700}

    retry = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=payload,
    )
    assert retry.status_code == 200
    assert retry.get_json()["idempotent"] is True
    assert retry.get_json()["hold"]["hold_id"] == hold["hold_id"]

    exhausted = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], request_id="maintenance-1/extra", amount=301),
    )
    assert exhausted.status_code == 409
    assert exhausted.get_json()["error"] == "budget_ceiling_exceeded"

    wrong_actor = client.post(
        f"/api/internal/infrastructure/budget-holds/{hold['hold_id']}/reconcile",
        headers=_service_headers("conductor"),
        json={"actual_cost_minor": 500, "operation_state": "stopped", "provider_job_id": "job-1"},
    )
    assert wrong_actor.status_code == 403

    settled = client.post(
        f"/api/internal/infrastructure/budget-holds/{hold['hold_id']}/reconcile",
        headers=_service_headers("continuum"),
        json={"actual_cost_minor": 500, "operation_state": "stopped", "provider_job_id": "job-1"},
    )
    assert settled.status_code == 200
    settled_hold = settled.get_json()["hold"]
    assert settled_hold["status"] == "settled"
    assert settled_hold["actual_cost"] == {"currency": "USD", "minor_units": 500}

    settled_retry = client.post(
        f"/api/internal/infrastructure/budget-holds/{hold['hold_id']}/reconcile",
        headers=_service_headers("continuum"),
        json={"actual_cost_minor": 500, "operation_state": "stopped", "provider_job_id": "job-1"},
    )
    assert settled_retry.get_json()["idempotent"] is True

    events = client.get(
        f"/api/billing/admin/infrastructure-budgets/{budget['budget_id']}/events",
        headers=_admin_headers(),
    )
    assert events.status_code == 200
    assert [event["event_type"] for event in events.get_json()["events"]] == [
        "budget.approved",
        "budget.hold_reserved",
        "budget.hold_reconciled",
    ]


def test_only_conductor_can_reserve_and_only_continuum_can_reconcile(monkeypatch, tmp_path):
    _app, client = _new_budget_app(monkeypatch, tmp_path)
    budget = _create_budget(client)
    denied = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("refiner"),
        json=_hold_payload(budget["budget_id"]),
    )
    assert denied.status_code == 403


def test_budget_window_provider_and_currency_are_enforced(monkeypatch, tmp_path):
    _app, client = _new_budget_app(monkeypatch, tmp_path)
    budget = _create_budget(client)

    denied_provider = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], provider="gcp"),
    )
    assert denied_provider.status_code == 403
    assert denied_provider.get_json()["error"] == "provider_not_approved"

    wrong_currency = _hold_payload(budget["budget_id"], request_id="maintenance-1/wrong-currency")
    wrong_currency["currency"] = "GBP"
    denied_currency = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=wrong_currency,
    )
    assert denied_currency.status_code == 400
    assert denied_currency.get_json()["error"] == "budget_currency_mismatch"


def test_actual_cost_overrun_is_recorded_and_consumes_budget(monkeypatch, tmp_path):
    _app, client = _new_budget_app(monkeypatch, tmp_path)
    budget = _create_budget(client, ceiling=1000)
    first = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], amount=600),
    )
    hold_id = first.get_json()["hold"]["hold_id"]
    overrun = client.post(
        f"/api/internal/infrastructure/budget-holds/{hold_id}/reconcile",
        headers=_service_headers("continuum"),
        json={"actual_cost_minor": 1100, "operation_state": "completed", "provider_job_id": "job-overrun"},
    )
    assert overrun.status_code == 200
    assert overrun.get_json()["hold"]["status"] == "overrun"

    blocked = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], request_id="maintenance-1/after-overrun", amount=1),
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == "budget_ceiling_exceeded"


def test_admin_can_disable_future_reservations_without_erasing_live_holds(monkeypatch, tmp_path):
    _app, client = _new_budget_app(monkeypatch, tmp_path)
    budget = _create_budget(client)
    reserved = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], amount=250),
    )
    hold_id = reserved.get_json()["hold"]["hold_id"]

    disabled = client.post(
        f"/api/billing/admin/infrastructure-budgets/{budget['budget_id']}/disable",
        headers=_admin_headers(),
        json={"reason": "Maintenance window cancelled"},
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["budget"]["status"] == "disabled"

    blocked = client.post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json=_hold_payload(budget["budget_id"], request_id="maintenance-1/new", amount=1),
    )
    assert blocked.status_code == 409
    assert blocked.get_json()["error"] == "budget_not_active"

    status = client.get(
        f"/api/internal/infrastructure/budget-holds/{hold_id}",
        headers=_service_headers("continuum"),
    )
    assert status.status_code == 200
    assert status.get_json()["hold"]["status"] == "reserved"

    released = client.post(
        f"/api/internal/infrastructure/budget-holds/{hold_id}/reconcile",
        headers=_service_headers("continuum"),
        json={"actual_cost_minor": 0, "operation_state": "stopped", "provider_job_id": "job-stopped"},
    )
    assert released.get_json()["hold"]["status"] == "released"


def test_unavailable_durable_storage_blocks_budget_mutations(monkeypatch, tmp_path):
    monkeypatch.delenv("BILLING_DATABASE_URL", raising=False)
    monkeypatch.setenv("BILLING_APP_TOKENS", "conductor=conductor-test-token")
    monkeypatch.setenv("BILLING_AUTH_OPEN", "1")
    monkeypatch.setenv("BILLING_REQUIRE_CUSTOMERS", "0")
    app = create_app()
    response = app.test_client().post(
        "/api/internal/infrastructure/budget-holds",
        headers=_service_headers("conductor"),
        json={"budget_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 503
    assert response.get_json()["error"] == "billing_storage_unavailable"
