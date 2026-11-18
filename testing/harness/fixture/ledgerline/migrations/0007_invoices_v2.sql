-- v2 invoice model: invoices replaces legacy_invoices.
CREATE TABLE invoices (
    id           BIGSERIAL PRIMARY KEY,
    merchant_id  BIGINT NOT NULL,
    customer_id  BIGINT NOT NULL,
    amount       NUMERIC(14,2) NOT NULL,
    currency     CHAR(3) NOT NULL,
    status       TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO invoices (merchant_id, customer_id, amount, currency, status, created_at)
SELECT merchant_id, customer_id, total, currency, state, created FROM legacy_invoices;
