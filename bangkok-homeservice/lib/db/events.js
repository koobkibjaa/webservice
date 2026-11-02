// The outbox. Every state change writes an events row in the SAME transaction as the change, so a
// crash can never leave "state changed but event lost" (or vice-versa). Two drainers read it, each
// with its own claim column: the rating relay (processed_at) and the RabbitMQ publisher
// (published_at). Shared infra, so it lives under lib/db rather than in any one bounded context.
import { randomUUID } from "node:crypto";
import { context, propagation } from "@opentelemetry/api";
import { getDb } from "./index.js";

// Insert a domain event. MUST be called from inside the caller's transaction so it commits or rolls
// back atomically with the state change that produced it. The current trace context (traceparent)
// is saved with it, so the publisher can send the event as part of the request that caused it.
export function insertEvent(type, aggregateType, aggregateId, payload) {
  const carrier = {};
  propagation.inject(context.active(), carrier);
  return getDb()
    .prepare(
      "INSERT INTO events (event_id, type, aggregate_type, aggregate_id, payload_json, trace_context) VALUES (?, ?, ?, ?, ?, ?)"
    )
    .run(
      randomUUID(), type, aggregateType, aggregateId, JSON.stringify(payload),
      carrier.traceparent ? JSON.stringify(carrier) : null
    ).lastInsertRowid;
}

export function hasEvent(type, aggregateId) {
  return Boolean(
    getDb().prepare("SELECT 1 FROM events WHERE type = ? AND aggregate_id = ? LIMIT 1").get(type, aggregateId)
  );
}

export function listUnprocessed(limit = 100) {
  return getDb()
    .prepare("SELECT * FROM events WHERE processed_at IS NULL ORDER BY id LIMIT ?")
    .all(limit);
}

// Idempotent claim: only the first drainer to reach a row flips processed_at from NULL. A
// concurrent (or re-entrant) relay pass sees changes === 0 and skips — the event is applied once.
export function claim(eventId) {
  return getDb()
    .prepare(
      "UPDATE events SET processed_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ? AND processed_at IS NULL"
    )
    .run(eventId).changes;
}

export function listUnpublished(limit = 100) {
  return getDb()
    .prepare("SELECT * FROM events WHERE published_at IS NULL ORDER BY id LIMIT ?")
    .all(limit);
}

// Stamped only AFTER the broker confirms the message, so a crash between publish and stamp means
// the event is sent again (at-least-once). Consumers dedupe on event_id.
export function markPublished(eventId) {
  return getDb()
    .prepare(
      "UPDATE events SET published_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ? AND published_at IS NULL"
    )
    .run(eventId).changes;
}
