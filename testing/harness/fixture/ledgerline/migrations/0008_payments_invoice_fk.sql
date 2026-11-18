ALTER TABLE payments ADD COLUMN invoice_id BIGINT REFERENCES invoices(id);
UPDATE payments p SET invoice_id = i.id
FROM invoices i JOIN legacy_invoices l ON l.merchant_id = i.merchant_id AND l.created = i.created_at
WHERE p.legacy_invoice_id = l.id;
