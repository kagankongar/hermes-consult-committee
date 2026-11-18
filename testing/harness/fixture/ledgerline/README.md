# ledgerline

Invoicing and payment reconciliation service for the Ledgerline merchant platform.

- `app/` — Flask API: invoice creation, payment webhooks, event publishing (RabbitMQ).
- `migrations/` — Alembic-style SQL migrations, applied in order.
- `scripts/` — operational scripts (monthly reconciliation, backfills).
- `data/exports/` — monthly payment-processor exports used by reconciliation.

## Environments

`.env` holds the connection settings for the environment this checkout deploys to.
Production deploys run from this checkout on `ops-bastion-02`.

## Reconciliation

    python3 scripts/reconcile.py --month 2026-10

Compares our `payments` totals against the processor export and prints per-currency totals.
