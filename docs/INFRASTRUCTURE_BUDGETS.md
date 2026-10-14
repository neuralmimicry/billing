# Infrastructure budgets and provider-cost holds

Billing owns the approved currency ceiling for temporary infrastructure.
Conductor may reserve part of that ceiling for a maintenance proposal; only
Continuum may reconcile the resulting provider operation. These routes record
budget authority and cost evidence. They do not call a provider or enable
provider mutations.

## Budget administration

An authenticated Billing control user creates a budget with
`POST /api/billing/admin/infrastructure-budgets`:

```json
{
  "idempotency_key": "qc04-maintenance-2026-10",
  "name": "qc04/qc05 maintenance capacity",
  "currency": "USD",
  "ceiling_minor": 25000,
  "providers": ["aws", "gcp"],
  "starts_at": "2026-10-20T00:00:00Z",
  "expires_at": "2026-10-21T00:00:00Z"
}
```

Creating the budget is the Billing administrator's explicit approval. Billing
stores the administrator as both creator and approver, and records an
immutable `budget.approved` event. A retry with the same idempotency key and
the same fields returns the existing budget; changed fields return a conflict.
Budgets use integer minor currency units, support GBP, USD and EUR, and are
limited to the approved AWS, GCP and Azure provider names. The only operation
on an approved budget is disabling new reservations; its existing holds remain
open until Continuum reconciles them. Ceiling changes require a separate new
budget approval rather than editing an existing approval.

`GET /api/billing/admin/infrastructure-budgets` lists budgets, and
`GET /api/billing/admin/infrastructure-budgets/{budget_id}/events` returns the
audit history. `POST /api/billing/admin/infrastructure-budgets/{budget_id}/disable`
requires a reason and prevents further holds without deleting or releasing
current holds.

## Reservation and reconciliation contract

Only the `conductor` internal app token may call
`POST /api/internal/infrastructure/budget-holds`. The request identifies the
approved budget, idempotent request ID, provider, currency, maximum cost,
expiry and maintenance proposal. Billing rejects a missing, disabled, expired,
not-yet-active or mismatched budget. It also rejects an unapproved provider,
currency mismatch, hold expiry past the budget expiry, and any reservation
that would make spent cost plus open holds plus the new hold exceed the
approved ceiling.

The PostgreSQL budget row is locked while Billing evaluates and creates a
hold, so concurrent reservations for the same budget cannot both spend the
same remaining ceiling. A reserved hold remains charged against the ceiling
until reconciled, including after its execution expiry; an expired lease alone
does not prove that provider capacity stopped.

Only the `continuum` internal app token may call
`POST /api/internal/infrastructure/budget-holds/{hold_id}/reconcile`. It must
report `actual_cost_minor`, a provider operation state and, when an operation
was started, the provider job ID. Reporting a zero-cost `not_started` or
`stopped` operation releases the hold. Other outcomes settle the actual cost.
If actual cost exceeds the reservation, Billing records `overrun` rather than
rejecting reconciliation; that actual amount counts against the ceiling and
subsequent reservations are denied whenever the ceiling is exhausted. Repeating
the exact reconciliation is idempotent; a conflicting outcome returns a
conflict. `GET /api/internal/infrastructure/budget-holds/{hold_id}` is
available to Conductor and Continuum.

The internal service token must be provisioned through the existing secret
management path, never placed in source control or browser storage. Billing
must have its configured Continuum PostgreSQL `BILLING_DATABASE_URL`; without
durable storage, every budget mutation returns `503`. The additive tables are
created by the existing `flask init-commerce-db` schema command.

## Current integration state

This adds the Billing-owned budget and cost-hold API contract, storage and
tests. Continuum does not yet submit or reconcile these holds, and Conductor
does not yet create them or plan a measured hybrid-capacity shortfall. Provider
profiles, pricing/expiry enforcement, authenticated service tokens and an
end-to-end no-charge simulation still need validation before any paid
capacity can be requested. Billing currently records Continuum's authenticated
actual-cost report and provider job ID; it does not independently verify that
report against a provider invoice/export or cap ancillary charges at the
provider. That evidence path must be added before describing reconciliation as
provider-verified. Do not enable cloud mutations or treat this API alone as
hybrid-capacity readiness.
