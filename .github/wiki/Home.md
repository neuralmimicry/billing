# Billing — Wiki Home

**Billing** is the dedicated token accounting and payment service for the NeuralMimicry platform. It owns account balances, ledger reads, payment capture, token grants/debits/cashouts, and the internal account APIs used by Refiner.

> ☕ [Support NeuralMimicry on Crowdfunder](https://www.crowdfunder.co.uk/p/qr/aWggxwPW?utm_campaign=sharemodal&utm_medium=referral&utm_source=shortlink)

---

## Quick navigation

| Page | Description |
|---|---|
| [Getting Started](Getting-Started) | Run Billing locally |
| [Token Model](Token-Model) | Balance types, ledger entries, cashout rules |
| [API Reference](API-Reference) | Account, event, payment, and ledger endpoints |
| [nmchain Integration](nmchain-Integration) | Writing ledger events to the private blockchain |
| [Configuration](Configuration) | Environment variables reference |
| [Contributing](Contributing) | Running tests, PR guidelines |

---

## Token model

Billing tracks two balance types:
- **Paid tokens** — purchased by the user; cashable
- **Free/grant tokens** — awarded by admin; not cashable

Balance never goes negative. Any shortfall is recorded on the ledger entry. Cashout operations draw only from the paid balance.

## Dependencies

Billing depends on:
- **Customers** — resolves browser identity via `/api/session` and verifies passwords via `/api/internal/credentials/verify`
- **nmchain** — records ledger, payment, and identity-linked events to the private blockchain when `BILLING_CHAIN_*` is configured

## Default port

`127.0.0.1:5020`

## Get involved

- 🐛 [Report a bug or request a feature](https://github.com/neuralmimicry/billing/issues)
- 💬 [Join the discussion](https://github.com/neuralmimicry/billing/discussions)
- 📧 Direct support: [info@neuralmimicry.ai](mailto:info@neuralmimicry.ai) · **£1,000/day + VAT**
- 🌐 [neuralmimicry.ai](https://neuralmimicry.ai)
