# Notification-service owns its own Postgres database. Two tables:
#   notifications     - moved here from the monolith's SQLite file (same columns)
#   processed_events  - the consumer's dedupe record: one row per event_id ever applied
from psycopg_pool import ConnectionPool

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
  id BIGSERIAL PRIMARY KEY,
  recipient_role TEXT NOT NULL CHECK (recipient_role IN ('customer','provider','admin')),
  recipient_id INTEGER NOT NULL,
  type TEXT NOT NULL,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  order_id INTEGER,
  read_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_notifications_recipient
  ON notifications (recipient_role, recipient_id, id DESC);

CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

pool = ConnectionPool(config.DATABASE_URL, min_size=1, max_size=5, open=False)


def init():
    pool.open(wait=True, timeout=30)
    with pool.connection() as conn:
        conn.execute(SCHEMA)


def iso(ts):
    # Same shape the monolith returned: 2026-10-27T13:57:41.123Z
    return None if ts is None else ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


def public(row):
    return {
        "id": row[0],
        "type": row[1],
        "title": row[2],
        "body": row[3],
        "order_id": row[4],
        "read_at": iso(row[5]),
        "created_at": iso(row[6]),
    }


COLUMNS = "id, type, title, body, order_id, read_at, created_at"


def list_for_recipient(role, recipient_id):
    with pool.connection() as conn:
        rows = conn.execute(
            f"SELECT {COLUMNS} FROM notifications WHERE recipient_role = %s AND recipient_id = %s ORDER BY id DESC",
            (role, recipient_id),
        ).fetchall()
    return [public(r) for r in rows]


def find_owner(notification_id):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT recipient_role, recipient_id FROM notifications WHERE id = %s", (notification_id,)
        ).fetchone()


def mark_read(notification_id, role, recipient_id):
    with pool.connection() as conn:
        conn.execute(
            "UPDATE notifications SET read_at = now() WHERE id = %s AND recipient_role = %s AND recipient_id = %s AND read_at IS NULL",
            (notification_id, role, recipient_id),
        )
        row = conn.execute(f"SELECT {COLUMNS} FROM notifications WHERE id = %s", (notification_id,)).fetchone()
    return public(row)


def apply_event(event_id, event_type, notifications):
    """Record the event and insert its notifications in ONE transaction. Returns False when the
    event_id was already applied (a duplicate delivery), in which case nothing is written."""
    with pool.connection() as conn:
        with conn.transaction():
            claimed = conn.execute(
                "INSERT INTO processed_events (event_id, type) VALUES (%s, %s) ON CONFLICT (event_id) DO NOTHING",
                (event_id, event_type),
            ).rowcount
            if claimed == 0:
                return False
            for n in notifications:
                conn.execute(
                    "INSERT INTO notifications (recipient_role, recipient_id, type, title, body, order_id) VALUES (%s, %s, %s, %s, %s, %s)",
                    (n["role"], n["id"], n["type"], n["title"], n["body"], n.get("order_id")),
                )
    return True


def ping():
    with pool.connection() as conn:
        conn.execute("SELECT 1")
