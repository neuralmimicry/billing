# Billing

## Sponsor NeuralMimicry

This service provides open, auditable token accounting, payment capture, and ledger primitives for the NeuralMimicry platform — designed from the outset to be deployable independently of any single product. NeuralMimicry is an independent open-source initiative and we rely on community support to sustain this work.

**[☕ Support us on Crowdfunder](https://www.crowdfunder.co.uk/p/qr/aWggxwPW?utm_campaign=sharemodal&utm_medium=referral&utm_source=shortlink)**

---

Billing is NeuralMimicry's dedicated token and settlement service.
It owns account balances, ledger reads, payment capture, token grants/debits/cashouts, and the internal account APIs used by Refiner.

## Why this repo exists

The billing and token-ledger code was extracted from Refiner so token accounting can be deployed, audited, and evolved independently from workflow execution.

The service split is now:

- Refiner: `${NM_LOCAL_REPO_ROOT}/rag_demo`
- Customers: `${NM_LOCAL_REPO_ROOT}/customers`
- Billing: `${NM_LOCAL_REPO_ROOT}/billing`
- nmstt: `${NM_LOCAL_REPO_ROOT}/nmstt`
- nmchain: `${NM_LOCAL_REPO_ROOT}/nmchain`

The public website still talks to `https://api.neuralmimicry.ai`.
Refiner remains the single public backend origin and proxies user-facing token routes to Billing.

## Topology

Public traffic:

1. `https://neuralmimicry.ai` renders the commercial frontend
2. The frontend calls `https://api.neuralmimicry.ai`
3. Traffic enters through `vega.neuralmimicry.ai`
4. Internal routing continues to `spirit.neuralmimicry.ai` and the tenant Kubernetes workloads
5. Refiner proxies `/api/tokens` and `/api/tokens/ledger` to Billing when configured

Internal service dependencies:

- Billing resolves browser identity through Customers `/api/session`
- Billing verifies passwords through Customers `/api/internal/credentials/verify`
- Billing writes ledger, payment, and identity-linked events to nmchain
- Refiner calls Billing internal account/event/payment endpoints with a Customers-issued service-account bearer token for non-browser workflows

## Responsibilities

Billing owns:

- personal token balance review
- token purchase capture
- token grants and cash-out accounting
- token reservation, debit, release, and sync writes initiated by Refiner
- personal and team account snapshots
- personal and team ledger reads

Billing does not own:

- account registration or login
- browser session cookies
- job execution orchestration
- speech-to-text

## API surface

Public routes:

- `GET /billing`
- `GET /billing/admin`
- `GET /billing/assets/<filename>`
- `GET /api/billing/dashboard/customer`
- `GET /api/billing/dashboard/admin`
- `GET /api/health`
- `GET /api/version`
- `GET|POST /api/tokens`
- `GET /api/tokens/ledger`

Internal routes protected by trusted internal bearer tokens:

- `GET /api/internal/accounts/<scope>/<account_id>`
- `GET /api/internal/accounts/<scope>/<account_id>/ledger`
- `POST /api/internal/accounts/<scope>/<account_id>/events`
- `POST /api/internal/payments`

## Ledger model

Billing treats nmchain as the ledger of record.
The service normalizes the chain response into the token-account payloads already expected by the existing frontend and Refiner code.

Important behaviour preserved from the embedded implementation:

- balances do not go negative
- shortfalls are tracked explicitly
- personal and team scopes are separate
- free grants are tracked separately from paid balances
- public token routes remain backward compatible for the website and Refiner UI

## Authentication and authorisation

Billing uses two modes of identity verification:

- browser/user routes call Customers `/api/session`
- protected internal mutation routes require either a Billing app token or a trusted Customers-issued service-account token whose `service_key` matches the internal allow-list

Password-protected actions such as top-up confirmation, grants, and cashouts use Customers `/api/internal/credentials/verify` so password truth stays in the identity service.

Billing now consumes the resolved `service_access` contract emitted by Customers instead of relying only on `role == admin`.

Load-bearing service checks:

- `billing:use` is required for customer dashboard, token balance, token ledger, transfer, top-up, and cash-out flows
- `billing:control` is required for the operator/admin dashboard and token grants

Compatibility behaviour during rollout:

- if Customers already returns `service_access`, Billing uses it directly
- if Billing is pointed at an older Customers payload without `service_access`, authenticated human users still fall back to `billing:use`
- Customers-issued service-account bearer tokens never inherit that human fallback and must be granted `service_access.billing` explicitly
- global `admin` role or `admin` group still fall back to `billing:control`
- development headers can inject `X-Debug-Service-Access: billing=control` for local verification

## Billing Intelligence dashboards

The service now exposes a dedicated NeuralMimicry-branded billing dashboard surface for both customer and operator use.

Customer-facing routes:

- `GET /billing`
- `GET /api/billing/dashboard/customer`

Admin/operator routes:

- `GET /billing/admin`
- `GET /api/billing/dashboard/admin`

Dashboard assets:

- `GET /billing/assets/dashboard.css`
- `GET /billing/assets/dashboard.js`

Design and runtime characteristics:

- browser-facing HTML and assets are namespaced under `/billing` so Refiner can proxy them cleanly from `https://api.neuralmimicry.ai`
- customer views show balance posture, statement periods, payment methods, transactions, service mix, and anomaly posture
- admin views add recent portfolio state, settlement concentration, highest-movement accounts, and an anomaly review queue
- transaction and statement exports stay first-party and browser-generated; no third-party dashboard runtime is required

### Anomaly engine

Billing dashboards do not use generic off-the-shelf anomaly widgets.
They combine NeuralMimicry-native approaches instead:

- a Tracey-style fuzzy scorer with type-n uncertainty envelopes for spend, settlement, provider churn, shortfall, dormancy, and reserve pressure
- an AARNN-inspired compact recurrent scorer (`NeuromimicPulseModel`) that tracks behavioural drift, activation, and novelty across recent billing sequences
- a fused posture model that produces risk, confidence, top signals, and operator actions for customer and admin dashboards alike

Load-bearing implementation files:

- `${NM_LOCAL_REPO_ROOT}/billing/billing_service/dashboard_anomaly.py`
- `${NM_LOCAL_REPO_ROOT}/billing/billing_service/dashboard_analytics.py`
- `${NM_LOCAL_REPO_ROOT}/billing/billing_service/templates/dashboard.html`
- `${NM_LOCAL_REPO_ROOT}/billing/billing_service/static/dashboard.css`
- `${NM_LOCAL_REPO_ROOT}/billing/billing_service/static/dashboard.js`

## Auditing with nmchain

Billing writes the load-bearing financial and ledger events into nmchain:

- token mutations
- payment capture
- identity-linked balance updates

That creates an auditable chain between:

- who the user is, from Customers
- what service was used, from Refiner/nmstt
- what balance changed, from Billing
- what immutable event was recorded, from nmchain

Billing remains stateless for balance truth because the ledger of record is nmchain.
Relevant relational customer/session records continue to live in Customers, backed by the Continuum Postgres tenant service.
For the wider split platform, any future Billing-owned relational records such as operator review notes, report scheduling state, or settlement workflow state should use the same Continuum Postgres tenant service rather than pod-local files.
Any durable Billing-side artefacts that must survive pod replacement should use the Continuum NFS-backed shared storage, not container-local paths.
The current dashboard implementation does not introduce a new durable store because:

- immutable financial/accounting truth already lives in nmchain
- identity-linked relational truth already lives in Customers/Postgres
- dashboard exports are generated client-side rather than persisted inside Billing pods

## Configuration

Core runtime variables:

- `BILLING_HOST`
- `BILLING_PORT`
- `BILLING_TOKEN_BTC_RATE`
- `BILLING_APP_TOKENS`
- `BILLING_REQUIRE_CUSTOMERS`
- `BILLING_AUTH_OPEN`
- `BILLING_CORS_ORIGINS`

Customers integration:

- `BILLING_CUSTOMERS_API_BASE`
- `BILLING_CUSTOMERS_API_TOKEN` (prefer the Billing Customers-issued service-account token; legacy app tokens remain accepted)
- `BILLING_CUSTOMERS_TIMEOUT`

nmchain integration:

- `BILLING_CHAIN_API_BASE`
- `BILLING_CHAIN_API_TOKEN` (prefer the Billing Customers-issued service-account token when nmchain is using central session validation)
- `BILLING_CHAIN_APP_ID`
- `BILLING_CHAIN_TIMEOUT`

Shared site context:

- `NEURALMIMICRY_SITE_BASE`

Dashboard tuning:

- `BILLING_DASHBOARD_LEDGER_LIMIT`
- `BILLING_DASHBOARD_BLOCK_LIMIT`
- `BILLING_DASHBOARD_ACCOUNT_LIMIT`
- `BILLING_DASHBOARD_ASSET_CACHE_SECONDS`
- `BILLING_ANOMALY_FUZZY_ENABLED`
- `BILLING_ANOMALY_FUZZY_ORDER`
- `BILLING_ANOMALY_FUZZY_UNCERTAINTY`
- `BILLING_ANOMALY_FUZZY_EDGE_BIAS`
- `BILLING_ANOMALY_FUZZY_AARNN_WEIGHT`
- `BILLING_ANOMALY_FUZZY_SECURITY_WEIGHT`
- `BILLING_ANOMALY_NEURO_RECURRENCE`
- `BILLING_ANOMALY_NEURO_DRIFT_WEIGHT`
- `BILLING_ANOMALY_NEURO_ACTIVATION_WEIGHT`
- `BILLING_ANOMALY_NEURO_NOVELTY_WEIGHT`
- `BILLING_ANOMALY_MIN_RISK`
- `BILLING_ANOMALY_MIN_CONFIDENCE`

## Local development

Billing targets Python 3.13.

Create a virtual environment and install the package:

```bash
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

Development example using an open auth stub:

```bash
export BILLING_APP_TOKENS='refiner=dev-refiner-token'
export BILLING_AUTH_OPEN=1
export BILLING_CHAIN_API_BASE='http://127.0.0.1:9080'
export BILLING_CHAIN_API_TOKEN='dev-chain-token'
python -m billing_service
```

Health check:

```bash
curl http://127.0.0.1:5020/api/health
```

## Container build

```bash
podman build -t ghcr.io/neuralmimicry/billing:latest -f Containerfile .
```

## Continuum deployment

Tenant playbook:

- `${SWARMHPC_ROOT}/swarmhpc/ansible/continuum_tenant_billing_site.yml`

Role:

- `roles/continuum_tenant_billing`

Deployment defaults assume:

- internal service URL `http://billing.billing.svc.cluster.local:5020`
- a generated `refiner` app token for compatibility and a Customers-issued `refiner` service-account token for primary internal account/event/payment calls
- Customers reachable at `http://customers.customers.svc.cluster.local:5010`
- nmchain reachable at `http://nmchain.nmchain.svc.cluster.local:9080`

## Interoperability contract

For the split platform to stay coherent:

- Refiner should proxy `/billing`, `/billing/admin`, `/billing/assets/*`, `/api/billing/dashboard/customer`, and `/api/billing/dashboard/admin` to Billing
- Refiner should proxy user token routes to Billing
- Refiner should call Billing internal routes with its own Customers-issued service-account token
- Billing should resolve session and password truth from Customers
- Billing should record final account events into nmchain

See also:

- `${NM_LOCAL_REPO_ROOT}/rag_demo/SERVICE_SPLIT_ARCHITECTURE.md`
