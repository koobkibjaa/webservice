// The monolith's RabbitMQ consumer: events the extracted services send back to it. Today one:
//   payment.succeeded (payment-service) -> Booking marks the order paid
// Started once from instrumentation.js; never under test. Each message is acked only after the
// handler's transaction commits, so a crash redelivers it; handlers are idempotent. A message
// that cannot be parsed goes to a dead-letter queue instead of being redelivered forever.
import amqp from "amqplib";
import { config } from "../config.js";
import { logger } from "../logger.js";
import { EXCHANGE } from "./publisher.js";
import { applyPaymentSucceeded } from "../modules/booking/service.js";

const CONSUMER = Symbol.for("edemand.consumer");
const QUEUE = "monolith.events";
const DLX = "monolith.dlx";
const DEAD = "monolith.events.dead";

const HANDLERS = {
  "payment.succeeded": applyPaymentSucceeded,
};

async function run(state) {
  while (!state.stopped) {
    let conn;
    try {
      conn = await amqp.connect(config.rabbitmqUrl);
      const channel = await conn.createChannel();
      await channel.assertExchange(EXCHANGE, "topic", { durable: true });
      await channel.assertExchange(DLX, "fanout", { durable: true });
      await channel.assertQueue(DEAD, { durable: true });
      await channel.bindQueue(DEAD, DLX, "");
      await channel.assertQueue(QUEUE, { durable: true, deadLetterExchange: DLX });
      for (const key of Object.keys(HANDLERS)) await channel.bindQueue(QUEUE, EXCHANGE, key);
      await channel.prefetch(10);

      await channel.consume(QUEUE, (msg) => {
        let event;
        try {
          event = JSON.parse(msg.content.toString());
          HANDLERS[event.type](event.payload);
        } catch (err) {
          logger.error({ err: err.message, type: event?.type }, "consumer: message dead-lettered");
          channel.reject(msg, false);
          return;
        }
        logger.info({ eventId: event.event_id, type: event.type }, "consumer: event applied");
        channel.ack(msg);
      });
      logger.info({ queue: QUEUE }, "consumer: connected to RabbitMQ");
      await new Promise((resolve) => {
        conn.on("close", resolve);
        conn.on("error", (err) => logger.warn({ err: err.message }, "consumer: connection error"));
      });
    } catch (err) {
      logger.warn({ err: err.message }, "consumer: broker unavailable, retrying");
    }
    try { await conn?.close(); } catch { /* already closed */ }
    if (!state.stopped) await new Promise((r) => setTimeout(r, 2000));
  }
}

export function startConsumer() {
  if (globalThis[CONSUMER]) return;
  const state = { stopped: false };
  globalThis[CONSUMER] = state;
  run(state);
  logger.info("event consumer started");
}
