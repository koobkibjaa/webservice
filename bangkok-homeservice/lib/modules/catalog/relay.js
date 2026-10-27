// The rating relay — the in-process drainer of the shared `events` table (processed_at). Started
// once from instrumentation.js as setInterval(runRelayOnce, 1000); also exported so tests can drain
// it synchronously. Every domain event is claimed exactly once (processed_at) before dispatch, so a
// second pass over the same event is a no-op — the relay is idempotent.
//
// THE rating aggregate is recomputed HERE, driven by the review.created event, never inline in the
// review handler. That is the deliberate eventual-consistency contrast the course teaches.
//
// Notifications used to be written here too. They moved to notification-service, which receives
// the same events through RabbitMQ (messaging/publisher.js), so this relay ignores every other type.
import { listUnprocessed, claim } from "../../db/events.js";
import { logger } from "../../logger.js";
import * as catalogRepository from "./repository.js";
import * as providersRepository from "../providers/repository.js";

const RELAY = Symbol.for("edemand.relay.interval");

function dispatch(event) {
  if (event.type !== "review.created") return;
  const payload = JSON.parse(event.payload_json);
  // Eventual consistency: recompute the service AND provider aggregates from the reviews table.
  catalogRepository.recomputeRatingAggregate(payload.service_id);
  providersRepository.recomputeRatingAggregate(payload.provider_id);
}

// Drain up to 100 unprocessed events. Each is claimed (processed_at) before dispatch; a concurrent
// or repeated pass that loses the claim skips the event. Returns the number of events processed.
export function runRelayOnce() {
  const rows = listUnprocessed(100);
  let processed = 0;
  for (const event of rows) {
    if (claim(event.id) === 0) continue; // someone else already claimed it
    try {
      dispatch(event);
      processed += 1;
    } catch (err) {
      logger.error({ err: err.message, eventId: event.id }, "relay: dispatch failed");
    }
  }
  return processed;
}

export function startRelay() {
  if (globalThis[RELAY]) return;
  const handle = setInterval(() => {
    try {
      runRelayOnce();
    } catch (err) {
      logger.error({ err: err.message }, "relay: pass failed");
    }
  }, 1000);
  // Don't keep the process alive solely for the relay.
  if (typeof handle.unref === "function") handle.unref();
  globalThis[RELAY] = handle;
  logger.info("rating relay started");
}

export function stopRelay() {
  if (globalThis[RELAY]) {
    clearInterval(globalThis[RELAY]);
    globalThis[RELAY] = undefined;
  }
}
