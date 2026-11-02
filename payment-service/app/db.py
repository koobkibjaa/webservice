# Payment-service owns its own Postgres database. The four Payments tables moved here from the
# monolith (same columns, plus customer_id/provider_id on the intent, which it used to read from
# the monolith's orders table), and two tables of its own:
#   processed_events - consumer dedupe: one row per RabbitMQ event_id ever applied
#   outbox           - events this service emits (payment.succeeded), written in the same
#                      transaction as the change and sent to RabbitMQ by publisher.py
import json
import uuid

from opentelemetry import propagate
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config
from .errors import ApiError

SCHEMA = """
CREATE TABLE IF NOT EXISTS payment_intents (
  id BIGSERIAL PRIMARY KEY,
  order_id INTEGER NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL,
  provider_id INTEGER NOT NULL,
  amount_cents INTEGER NOT NULL,
  gateway_ref TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'requires_payment'
    CHECK (status IN ('requires_payment','succeeded','failed','cancelled')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS transactions (
  id BIGSERIAL PRIMARY KEY,
  order_id INTEGER,
  customer_id INTEGER,
  provider_id INTEGER,
  kind TEXT NOT NULL CHECK (kind IN ('charge','payout_credit','commission_owed','refund')),
  amount_cents INTEGER NOT NULL,
  method TEXT NOT NULL CHECK (method IN ('cod','gateway')),
  gateway_ref TEXT,
  status TEXT NOT NULL DEFAULT 'succeeded' CHECK (status IN ('pending','succeeded','failed','refunded')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transactions_provider ON transactions (provider_id);
CREATE INDEX IF NOT EXISTS idx_transactions_customer ON transactions (customer_id);
CREATE TABLE IF NOT EXISTS provider_wallets (
  provider_id INTEGER PRIMARY KEY,
  balance_cents INTEGER NOT NULL DEFAULT 0,
  commission_owed_cents INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS webhook_events (
  id BIGSERIAL PRIMARY KEY,
  gateway_event_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS outbox (
  id BIGSERIAL PRIMARY KEY,
  event_id TEXT NOT NULL UNIQUE,
  type TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  published_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_outbox_unpublished ON outbox (id) WHERE published_at IS NULL;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS trace_context TEXT;
CREATE TABLE IF NOT EXISTS idempotency_keys (
  key TEXT PRIMARY KEY,
  request_hash TEXT NOT NULL,
  status_code INTEGER,
  response_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=8, open=False, kwargs={"row_factory": dict_row})


def init():
    pool.open(wait=True, timeout=30)
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def ping():
    with pool.connection() as conn:
        conn.execute("SELECT 1")


def iso(ts):
    # Same shape the monolith returned: 2026-10-27T13:57:41.123Z
    return None if ts is None else ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def public_intent(row):
    return {k: row[k] for k in ("id", "order_id", "amount_cents", "gateway_ref", "status")}


def public_transaction(row):
    out = {k: row[k] for k in ("id", "order_id", "kind", "amount_cents", "method", "gateway_ref", "status")}
    out["created_at"] = iso(row["created_at"])
    return out


def _emit(conn, type_, aggregate_type, aggregate_id, payload):
    # The current trace context goes with the event, so publisher.py can send it as part of the
    # request that caused it (it is published later, outside that request).
    carrier = {}
    propagate.inject(carrier)
    conn.execute(
        "INSERT INTO outbox (event_id, type, aggregate_type, aggregate_id, payload_json, trace_context) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (str(uuid.uuid4()), type_, aggregate_type, aggregate_id, json.dumps(payload),
         json.dumps(carrier) if carrier else None),
    )


# ---------- intents (called by the monolith while it places a gateway booking) ----------

def create_intent(idempotency_key, order_id, customer_id, provider_id, amount_cents):
    """Saved once per Idempotency-Key. The key is claimed, the intent written and the reply stored
    in ONE transaction, so a retry of a call whose reply was lost gets the stored reply back
    instead of a second write. Two calls racing on one key: the second one's INSERT waits for the
    first to commit, then finds the key taken and replays. Returns (status_code, intent, replayed)."""
    request = json.dumps([order_id, customer_id, provider_id, amount_cents])
    with pool.connection() as conn, conn.transaction():
        claimed = conn.execute(
            "INSERT INTO idempotency_keys (key, request_hash) VALUES (%s, md5(%s)) ON CONFLICT (key) DO NOTHING",
            (idempotency_key, request),
        ).rowcount
        if not claimed:
            seen = conn.execute(
                "SELECT request_hash = md5(%s) AS same, status_code, response_json FROM idempotency_keys WHERE key = %s",
                (request, idempotency_key),
            ).fetchone()
            if not seen["same"]:
                raise ApiError(422, "Idempotency-Key was already used for a different request")
            return seen["status_code"], json.loads(seen["response_json"]), True

        row = conn.execute(
            """INSERT INTO payment_intents (order_id, customer_id, provider_id, amount_cents, gateway_ref)
               VALUES (%s, %s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING RETURNING *""",
            (order_id, customer_id, provider_id, amount_cents, f"SIM-PI-{uuid.uuid4()}"),
        ).fetchone()
        status = 201
        if row is None:  # a different key for an order that already has an intent
            row = conn.execute("SELECT * FROM payment_intents WHERE order_id = %s", (order_id,)).fetchone()
            if row["amount_cents"] != amount_cents or row["customer_id"] != customer_id:
                raise ApiError(409, f"order {order_id} already has a different payment intent")
            status = 200
        intent = public_intent(row)
        conn.execute(
            "UPDATE idempotency_keys SET status_code = %s, response_json = %s WHERE key = %s",
            (status, json.dumps(intent), idempotency_key),
        )
    return status, intent, False


def find_intent_by_order(order_id):
    with pool.connection() as conn:
        return conn.execute("SELECT * FROM payment_intents WHERE order_id = %s", (order_id,)).fetchone()


# ---------- the gateway webhook: the replay dedupe ----------

def handle_webhook(gateway_event_id, type_, gateway_ref, payload):
    """One transaction. A replayed gateway_event_id hits the UNIQUE constraint and does nothing."""
    with pool.connection() as conn, conn.transaction():
        fresh = conn.execute(
            "INSERT INTO webhook_events (gateway_event_id, type, payload_json) VALUES (%s, %s, %s) "
            "ON CONFLICT (gateway_event_id) DO NOTHING",
            (gateway_event_id, type_, json.dumps(payload)),
        ).rowcount
        if not fresh:
            return {"replayed": True}

        intent = conn.execute(
            "SELECT * FROM payment_intents WHERE gateway_ref = %s FOR UPDATE", (gateway_ref,)
        ).fetchone()
        if not intent:
            raise ApiError(404, f"payment intent {gateway_ref} not found")  # rolls back the insert too

        # Conditional flip: an already-settled (or cancelled) intent matches 0 rows and is not charged.
        flipped = conn.execute(
            "UPDATE payment_intents SET status = 'succeeded' WHERE id = %s AND status = 'requires_payment'",
            (intent["id"],),
        ).rowcount
        mark = "UPDATE webhook_events SET processed_at = now() WHERE gateway_event_id = %s"
        if not flipped:
            conn.execute(mark, (gateway_event_id,))
            return {"replayed": False, "already_settled": True}

        conn.execute(
            """INSERT INTO transactions (order_id, customer_id, provider_id, kind, amount_cents, method, gateway_ref, status)
               VALUES (%s, %s, %s, 'charge', %s, 'gateway', %s, 'succeeded')""",
            (intent["order_id"], intent["customer_id"], intent["provider_id"], intent["amount_cents"], gateway_ref),
        )
        # The monolith marks its order paid when it receives this; notification-service tells the provider.
        _emit(conn, "payment.succeeded", "order", intent["order_id"], {
            "order_id": intent["order_id"], "customer_id": intent["customer_id"],
            "provider_id": intent["provider_id"], "amount_cents": intent["amount_cents"],
        })
        conn.execute(mark, (gateway_event_id,))
        return {"replayed": False, "settled": True}


# ---------- reads ----------

def wallet(provider_id):
    with pool.connection() as conn:
        w = conn.execute("SELECT * FROM provider_wallets WHERE provider_id = %s", (provider_id,)).fetchone()
        txs = conn.execute(
            "SELECT * FROM transactions WHERE provider_id = %s ORDER BY id DESC", (provider_id,)
        ).fetchall()
    return {
        "provider_id": provider_id,
        "balance_cents": w["balance_cents"] if w else 0,
        "commission_owed_cents": w["commission_owed_cents"] if w else 0,
        "transactions": [public_transaction(t) for t in txs],
    }


def customer_transactions(customer_id):
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE customer_id = %s ORDER BY id DESC", (customer_id,)
        ).fetchall()
    return [public_transaction(t) for t in rows]


# ---------- events from the monolith ----------

def apply_event(event_id, type_, payload):
    """Apply one booking event exactly once: the dedupe row and the money movement share one
    transaction. Returns False for a duplicate delivery (nothing written)."""
    with pool.connection() as conn, conn.transaction():
        claimed = conn.execute(
            "INSERT INTO processed_events (event_id, type) VALUES (%s, %s) ON CONFLICT (event_id) DO NOTHING",
            (event_id, type_),
        ).rowcount
        if not claimed:
            return False
        if type_ == "booking.completed":
            _settle_completed(conn, payload)
        elif type_ == "booking.cancelled":
            _settle_cancelled(conn, payload)
    return True


def _wallet_add(conn, provider_id, balance=0, owed=0):
    # The wallet is created the first time money touches it (no more empty wallet at registration).
    conn.execute(
        """INSERT INTO provider_wallets (provider_id, balance_cents, commission_owed_cents) VALUES (%s, %s, %s)
           ON CONFLICT (provider_id) DO UPDATE SET
             balance_cents = provider_wallets.balance_cents + EXCLUDED.balance_cents,
             commission_owed_cents = provider_wallets.commission_owed_cents + EXCLUDED.commission_owed_cents,
             updated_at = now()""",
        (provider_id, balance, owed),
    )


def _settle_completed(conn, p):
    # Commission split, unchanged from the monolith: a gateway job's earnings are payable to the
    # provider; on a cash (COD) job the provider owes the platform its commission.
    tx = "INSERT INTO transactions (order_id, customer_id, provider_id, kind, amount_cents, method) VALUES (%s, %s, %s, %s, %s, %s)"
    if p["payment_method"] == "gateway":
        _wallet_add(conn, p["provider_id"], balance=p["provider_earnings_cents"])
        conn.execute(tx, (p["order_id"], p["customer_id"], p["provider_id"], "payout_credit", p["provider_earnings_cents"], "gateway"))
    else:
        _wallet_add(conn, p["provider_id"], owed=p["commission_cents"])
        conn.execute(tx, (p["order_id"], p["customer_id"], p["provider_id"], "commission_owed", p["commission_cents"], "cod"))


def _settle_cancelled(conn, p):
    # An unpaid intent can no longer be paid; a paid one is refunded.
    conn.execute(
        "UPDATE payment_intents SET status = 'cancelled' WHERE order_id = %s AND status = 'requires_payment'",
        (p["order_id"],),
    )
    if p.get("refunded"):
        conn.execute(
            """INSERT INTO transactions (order_id, customer_id, provider_id, kind, amount_cents, method, status)
               VALUES (%s, %s, %s, 'refund', %s, 'gateway', 'refunded')""",
            (p["order_id"], p["customer_id"], p["provider_id"], p["total_cents"]),
        )


# ---------- outbox, drained by publisher.py ----------

def claim_unpublished(conn, limit=100):
    return conn.execute(
        "SELECT * FROM outbox WHERE published_at IS NULL ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED", (limit,)
    ).fetchall()


def mark_published(conn, ids):
    conn.execute("UPDATE outbox SET published_at = now() WHERE id = ANY(%s)", (ids,))
