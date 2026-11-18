from flask import Blueprint, request
from .db import connect
from .events import publish

bp = Blueprint("webhooks", __name__)

@bp.post("/webhooks/processor")
def processor_webhook():
    evt = request.get_json()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO payments (invoice_id, processor_ref, amount, currency, settled_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (processor_ref) DO NOTHING",
                (evt["invoice_id"], evt["ref"], evt["amount"], evt["currency"], evt["settled_at"]),
            )
        conn.commit()
    publish("payment.settled", {"invoice_id": evt["invoice_id"], "ref": evt["ref"]})
    return "", 204
