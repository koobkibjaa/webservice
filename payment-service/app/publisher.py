# The outbox publisher: sends rows of this service's outbox table to RabbitMQ, in a background
# thread. Same contract as the monolith's publisher: at-least-once. A row is stamped published only
# after the broker confirms it, inside the transaction that locked it, so a crash in between sends
# it again and consumers dedupe on event_id. FOR UPDATE SKIP LOCKED keeps two replicas of this
# service from sending the same row at the same time.
import json
import logging
import threading
import time

import pika
from opentelemetry import context, propagate

from . import config, db

log = logging.getLogger("payment-service.publisher")


def _message(row):
    return {
        "event_id": row["event_id"],
        "type": row["type"],
        "aggregate_type": row["aggregate_type"],
        "aggregate_id": row["aggregate_id"],
        "payload": json.loads(row["payload_json"]),
        "created_at": db.iso(row["created_at"]),
    }


def _drain(channel):
    with db.pool.connection() as conn, conn.transaction():
        rows = db.claim_unpublished(conn)
        for row in rows:
            # Publish inside the trace saved with the row; the pika instrumentation then puts it in
            # the message headers for the consumer.
            token = context.attach(propagate.extract(json.loads(row["trace_context"] or "{}")))
            try:
                channel.basic_publish(
                    config.EXCHANGE, row["type"], json.dumps(_message(row)),
                    pika.BasicProperties(content_type="application/json", delivery_mode=2,
                                         message_id=row["event_id"], type=row["type"], headers={}),
                )  # confirm_delivery: returns only once the broker has the message
            finally:
                context.detach(token)
        if rows:
            db.mark_published(conn, [r["id"] for r in rows])
            log.info(json.dumps({"msg": "events published", "count": len(rows)}))


def _run():
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(config.RABBITMQ_URL))
            channel = conn.channel()
            channel.exchange_declare(config.EXCHANGE, exchange_type="topic", durable=True)
            channel.confirm_delivery()
            log.info(json.dumps({"msg": "publisher connected"}))
            while True:
                _drain(channel)
                conn.sleep(0.5)  # also services the connection's heartbeats
        except Exception as err:  # broker or DB down: unpublished rows wait in the outbox
            log.warning(json.dumps({"msg": "publisher disconnected, retrying", "err": str(err)}))
        time.sleep(2)


def start():
    threading.Thread(target=_run, name="outbox-publisher", daemon=True).start()
