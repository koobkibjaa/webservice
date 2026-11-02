// Next.js boot hook. Runs once when the server process starts (dev and production alike). This is
// where the monolith gets its deterministic behaviour: a fresh, migrated, seeded database on every
// boot, the rating relay draining domain events in the background, and the outbox publisher
// sending the same events to RabbitMQ for the extracted services.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { bootstrapDb } = await import("./lib/db/bootstrap.js");
    bootstrapDb();
    const { startRelay } = await import("./lib/modules/catalog/relay.js");
    startRelay();
    const { startPublisher } = await import("./lib/messaging/publisher.js");
    startPublisher();
    const { startConsumer } = await import("./lib/messaging/consumer.js");
    startConsumer();
  }
}
