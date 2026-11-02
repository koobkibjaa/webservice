// Next.js boot hook. Runs once when the server process starts (dev and production alike). Tracing
// starts first, so what is imported after it is instrumented. Then the database (kept, or
// migrated and seeded when empty), the rating relay draining domain events in the background, the
// outbox publisher sending the same events to RabbitMQ for the extracted services, and the
// consumer for the events they send back.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { startTelemetry } = await import("./lib/telemetry.js");
    await startTelemetry();
    const { startMetricsServer } = await import("./lib/metrics.js");
    startMetricsServer();
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
