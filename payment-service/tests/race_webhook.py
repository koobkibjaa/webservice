# Race test (Area D): the payment-webhook race the bangkok-homeservice README names ("the real
# product has no idempotency and double-processes retries"). In the monolith, SQLite's single
# writer lock (BEGIN IMMEDIATE) serialized every webhook. In payment-service on Postgres, requests
# really do run at the same time, so the dedupe has to hold under concurrency on its own:
#   - the gateway_event_id claim is INSERT ... ON CONFLICT DO NOTHING (a racing duplicate waits for
#     the first insert to commit, then finds the conflict)
#   - the intent row is locked (SELECT ... FOR UPDATE) and flipped with a conditional UPDATE
#     (WHERE status = 'requires_payment'), so only one settlement can charge
#
# Fires two bursts, each at its own unpaid intent, every request released at the same instant by
# a barrier:
#   A. 25 callbacks with the SAME gateway_event_id   (the gateway retrying one event)
#   B. 25 callbacks with DIFFERENT gateway_event_ids (separate events, e.g. double clicks on
#      "Pay now", all for one payment)
# and checks that each order is charged exactly once and emits exactly one payment.succeeded.
#
#   docker compose exec payment-service python -m tests.race_webhook <order_a> <order_b>
#   (scripts/race-webhook.sh books the two orders and runs this)
import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from collections import Counter

from app import config, db

N = 25
URL = "http://127.0.0.1:8000/api/payments/webhook"


def fire(bodies):
    barrier = threading.Barrier(len(bodies))
    results = [None] * len(bodies)

    def one(i, body):
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST", headers={
            "content-type": "application/json", "x-webhook-secret": config.WEBHOOK_SECRET})
        barrier.wait()  # every thread sends at the same moment
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                results[i] = (res.status, json.loads(res.read()))
        except urllib.error.HTTPError as err:
            results[i] = (err.code, {})

    threads = [threading.Thread(target=one, args=(i, b)) for i, b in enumerate(bodies)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def outcome(result):
    status, body = result
    if status != 200:
        return f"HTTP {status}"
    return "settled" if body.get("settled") else "replayed" if body.get("replayed") else "already_settled"


def unpaid_ref(order_id):
    intent = db.find_intent_by_order(order_id)
    if intent is None or intent["status"] != "requires_payment":
        sys.exit(f"order {order_id} needs an unpaid payment intent (book with payment_method gateway)")
    return intent["gateway_ref"]


def main(order_a, order_b):
    db.pool.open(wait=True)
    ref_a, ref_b = unpaid_ref(order_a), unpaid_ref(order_b)

    same_id = f"RACE-SAME-{uuid.uuid4()}"
    a = fire([{"gateway_event_id": same_id, "type": "payment_intent.succeeded", "gateway_ref": ref_a}] * N)
    b = fire([{"gateway_event_id": f"RACE-{i}-{uuid.uuid4()}", "type": "payment_intent.succeeded",
               "gateway_ref": ref_b} for i in range(N)])

    with db.pool.connection() as conn:
        count = lambda sql, *args: conn.execute(sql, args).fetchone()["n"]
        charges = lambda o: count("SELECT count(*) AS n FROM transactions WHERE order_id = %s AND kind = 'charge'", o)
        events = lambda o: count("SELECT count(*) AS n FROM outbox WHERE type = 'payment.succeeded' AND aggregate_id = %s", o)
        same_rows = count("SELECT count(*) AS n FROM webhook_events WHERE gateway_event_id = %s", same_id)
        charges_a, events_a, charges_b, events_b = charges(order_a), events(order_a), charges(order_b), events(order_b)

    print(f"burst A on #{order_a}, {N} x the same gateway_event_id: {dict(Counter(map(outcome, a)))}")
    print(f"burst B on #{order_b}, {N} x different event ids:      {dict(Counter(map(outcome, b)))}")
    checks = [
        (f"A: charges for #{order_a}", 1, charges_a),
        (f"A: payment.succeeded events for #{order_a}", 1, events_a),
        ("A: webhook_events rows for the repeated id", 1, same_rows),
        (f"B: charges for #{order_b}", 1, charges_b),
        (f"B: payment.succeeded events for #{order_b}", 1, events_b),
        ("B: requests that settled", 1, sum(outcome(r) == "settled" for r in b)),
    ]
    failed = False
    for label, want, got in checks:
        ok = want == got
        failed |= not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {label:44} {got}" + ("" if ok else f" (expected {want})"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]))
