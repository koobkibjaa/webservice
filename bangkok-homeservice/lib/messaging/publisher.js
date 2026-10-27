// The outbox publisher: drains events (published_at IS NULL) to RabbitMQ. Started once from
// instrumentation.js; never under test. Delivery is at-least-once — a row is stamped published only
// after the broker confirms it, so a crash in between re-sends it and consumers dedupe on event_id.
//
// Topology: one durable topic exchange, routing key = event type (e.g. "booking.completed"). Each
// consuming service binds its own queue to the keys it cares about.
import amqp from "amqplib";
import { listUnpublished, markPublished } from "../db/events.js";
import { config } from "../config.js";
import { logger } from "../logger.js";

const PUBLISHER = Symbol.for("edemand.publisher");
export const EXCHANGE = "edemand.events";

function toMessage(event) {
  return {
    event_id: event.event_id,
    type: event.type,
    aggregate_type: event.aggregate_type,
    aggregate_id: event.aggregate_id,
    payload: JSON.parse(event.payload_json),
    created_at: event.created_at,
  };
}

async function publishPending(channel) {
  const rows = listUnpublished(100);
  if (rows.length === 0) return 0;
  for (const event of rows) {
    channel.publish(EXCHANGE, event.type, Buffer.from(JSON.stringify(toMessage(event))), {
      persistent: true,
      contentType: "application/json",
      messageId: event.event_id,
      type: event.type,
    });
  }
  await channel.waitForConfirms();
  for (const event of rows) markPublished(event.id);
  logger.info({ count: rows.length }, "publisher: events published");
  return rows.length;
}

// Connect, then loop. Any broker error drops the connection and the loop reconnects after a pause;
// unpublished rows simply wait in the outbox, so nothing is lost while RabbitMQ is down.
async function run(state) {
  while (!state.stopped) {
    let conn;
    try {
      conn = await amqp.connect(config.rabbitmqUrl);
      const channel = await conn.createConfirmChannel();
      await channel.assertExchange(EXCHANGE, "topic", { durable: true });
      logger.info("publisher: connected to RabbitMQ");
      let broken = false;
      conn.on("error", (err) => logger.warn({ err: err.message }, "publisher: connection error"));
      conn.on("close", () => { broken = true; });
      while (!state.stopped && !broken) {
        await publishPending(channel);
        await new Promise((r) => setTimeout(r, 500));
      }
    } catch (err) {
      logger.warn({ err: err.message }, "publisher: broker unavailable, retrying");
    }
    try { await conn?.close(); } catch { /* already closed */ }
    if (!state.stopped) await new Promise((r) => setTimeout(r, 2000));
  }
}

export function startPublisher() {
  if (globalThis[PUBLISHER]) return;
  const state = { stopped: false };
  globalThis[PUBLISHER] = state;
  run(state);
  logger.info("outbox publisher started");
}
