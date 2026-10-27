# RabbitMQ consumer, run in a background thread. Delivery from the monolith's outbox publisher is
# at-least-once, so the same event can arrive twice; db.apply_event dedupes on event_id inside the
# same transaction as the insert, and the message is acked only after that commit. A message that
# cannot be applied (bad JSON, missing field) is rejected to a dead-letter queue instead of being
# redelivered forever.
import json
import logging
import threading
import time

import pika

from . import config, db
from .events import ROUTING_KEYS, to_notifications

log = logging.getLogger("notification-service.consumer")
state = {"connected": False}


def _declare(channel):
    channel.exchange_declare(config.EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(config.DEAD_LETTER_EXCHANGE, exchange_type="fanout", durable=True)
    channel.queue_declare(config.DEAD_LETTER_QUEUE, durable=True)
    channel.queue_bind(config.DEAD_LETTER_QUEUE, config.DEAD_LETTER_EXCHANGE)
    channel.queue_declare(
        config.QUEUE, durable=True, arguments={"x-dead-letter-exchange": config.DEAD_LETTER_EXCHANGE}
    )
    for key in ROUTING_KEYS:
        channel.queue_bind(config.QUEUE, config.EXCHANGE, routing_key=key)


def _on_message(channel, method, properties, body):
    try:
        event = json.loads(body)
        applied = db.apply_event(event["event_id"], event["type"], to_notifications(event["type"], event["payload"]))
        log.info(json.dumps({"msg": "event applied" if applied else "duplicate event skipped",
                             "event_id": event["event_id"], "type": event["type"]}))
        channel.basic_ack(method.delivery_tag)
    except (ValueError, KeyError, TypeError) as err:
        log.error(json.dumps({"msg": "poison message dead-lettered", "err": str(err)}))
        channel.basic_reject(method.delivery_tag, requeue=False)


def _run():
    while True:
        try:
            conn = pika.BlockingConnection(pika.URLParameters(config.RABBITMQ_URL))
            channel = conn.channel()
            _declare(channel)
            channel.basic_qos(prefetch_count=10)
            channel.basic_consume(config.QUEUE, _on_message)
            state["connected"] = True
            log.info(json.dumps({"msg": "consumer connected", "queue": config.QUEUE}))
            channel.start_consuming()
        except Exception as err:  # broker down, DB down mid-message: reconnect, unacked messages redeliver
            log.warning(json.dumps({"msg": "consumer disconnected, retrying", "err": str(err)}))
        state["connected"] = False
        time.sleep(2)


def start():
    threading.Thread(target=_run, name="rabbitmq-consumer", daemon=True).start()
