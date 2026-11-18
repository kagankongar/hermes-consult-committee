from decimal import Decimal
from flask import Blueprint, request, jsonify
from .db import connect
from .events import publish

bp = Blueprint("invoices", __name__)

@bp.post("/invoices")
def create_invoice():
    body = request.get_json()
    amount = Decimal(body["amount"])
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO invoices (merchant_id, customer_id, amount, currency, status) "
                "VALUES (%s, %s, %s, %s, 'open') RETURNING id",
                (body["merchant_id"], body["customer_id"], amount, body["currency"]),
            )
            invoice_id = cur.fetchone()[0]
        conn.commit()
    # downstream: billing-notifier, revenue-reporting, dunning
    publish("invoice.created", {"id": invoice_id, "amount": str(amount), "currency": body["currency"]})
    return jsonify({"id": invoice_id}), 201
