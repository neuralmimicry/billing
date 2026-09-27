# Cardstream commerce

Orders and refund requests are durable PostgreSQL records. nmchain remains the
balance ledger. Apply the initial schema with
`flask --app billing_service.app:create_app init-commerce-db` in the configured
Python environment. See `commerce.env.example` for server configuration.

## Routes

All customer routes require `billing:use`; operator routes require `billing:control`.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/billing/catalog` | Availability, server prices, terms and policy version. |
| POST | `/api/billing/checkout` | Persist an intent and return a signed hosted form. |
| GET | `/api/billing/orders` | Own recent purchases and refund requests. |
| GET | `/api/billing/orders/<id>` | Own authoritative payment status. |
| POST | `/api/billing/orders/<id>/refunds` | Idempotent request against a confirmed purchase. |
| GET | `/api/billing/refunds/review` | Operator review queue. |
| POST | `/api/billing/refunds/<id>/review` | Customer-visible review or decline. |
| POST | `/api/billing/refunds/<id>/issue` | Explicitly approve a full original-card refund (`confirm: true`). |
| POST | `/api/billing/cardstream/callback` | Signed SALE/REFUND result, not cookie-authenticated. |
| GET/POST | `/api/billing/cardstream/return` | Redirect only; never credits a balance. |

Checkout requires `product_id`, `idempotency_key` (16–80 safe characters),
`accept_terms: true`, and the current `policy_version`. Store the key before
submitting and reuse it after a lost response. Amounts, currency, tokens, owner,
return URLs and provider references are server-controlled.

## Settlement and recovery

Purchases move from `pending`/`failed` to `paid` only after a signed matching SALE
result has been recorded in nmchain. The stable ledger key is
`cardstream-sale:<order-id>`. A lost ledger response produces HTTP 503, allowing
the same callback to recover without double credit. Merchant/amount/currency,
action, order and payment reference are validated. Payment references are unique.

Refunds begin as `requested` and can be `under_review` or `declined`. Creating or
reviewing a request does not move money. Explicit issuance has these stages:

1. Look up the latest Customers identity and refund hold. Unknown state fails closed.
2. Persist `debit_pending`; atomically withdraw the full original paid-token amount
   using `cardstream-refund-debit:<refund-id>`. Existing reservations are respected.
   Insufficient/ambiguous outcomes become `manual_review`, without a provider call.
3. Persist `debited`; recheck the hold, then commit `submitting` before the HTTP call.
4. Send one REFUND against the original Cardstream `xref`, with a unique refund
   reference. A signed success moves to `refunded`. Timeouts/unverified responses
   become `reconciliation_required`; repeated approval never resends the refund.
5. A signed provider decline commits `restore_pending`, restores the exact withdrawn
   paid tokens with `cardstream-refund-restore:<refund-id>`, then becomes
   `refund_failed`. Duplicate responses cannot restore twice.

`submitting` may mean the process stopped before or after the provider received the
request. Do not reset it or create a new refund to guess the outcome. Check the
original provider transaction and replay its authenticated callback to reconcile.
The same applies to `reconciliation_required`. A fresh operator issue request can
resume `debit_pending`/`debited` safely; it must never resend a `submitting` request.
If a failed refund is stuck in `restore_pending`, replay the signed failure callback
with its original reference to retry the ledger restoration.

The automatic path supports full refunds only, with sufficient unused paid tokens.
Partial refunds and exceptional ledger corrections require operator reconciliation.
This is a settlement constraint, not an invented customer eligibility policy.

## Refund holds

Customers owns `refund_hold_until`, defaulting to 48 hours after sensitive changes.
Confirmed hosted purchases notify Customers because checkout can introduce changed
card details. Event IDs deduplicate callback notifications. Other systems changing
payment details must call Customers' trusted payment-details change endpoint.
Never clear a hold by editing a local Billing row: issuance always rechecks Customers.

The initial deployment requires updated Customers (refund policy fields/change
notification) and updated nmchain (`atomic_cashout_v1`, `idempotent_entry_v1`).
Set `BILLING_CARDSTREAM_REFUNDS_ENABLED=1` only after validating the merchant's
Cardstream sandbox, including the signed direct REFUND response and callback.

## Tests

Run `pytest` using the project's Python 3.13 environment. For PostgreSQL concurrency
coverage, set `NM_COMMERCE_TEST_DATABASE_URL` to an isolated database whose name is
exactly `nm_commerce_test`. The tests recreate commerce tables in that database.
They simulate provider responses and never issue real charges or refunds.
