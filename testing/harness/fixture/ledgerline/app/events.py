import json
import os
import pika

_conn = None

def publish(routing_key: str, payload: dict) -> None:
    global _conn
    if _conn is None or _conn.is_closed:
        _conn = pika.BlockingConnection(pika.URLParameters(os.environ["RABBITMQ_URL"]))
    ch = _conn.channel()
    ch.basic_publish(exchange="ledger", routing_key=routing_key, body=json.dumps(payload))
