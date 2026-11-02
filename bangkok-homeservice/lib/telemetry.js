// OpenTelemetry tracing for the monolith (Area E). Started first thing in instrumentation.js,
// before anything that should be traced is imported; off unless OTEL_EXPORTER_OTLP_ENDPOINT is set
// (docker-compose sets it; the tests and `npm run dev` leave it unset).
//
// What produces spans:
//   - Next.js itself: one span per incoming request ("POST /api/bookings"), continuing the
//     caller's trace when a traceparent header arrives through the gateway
//   - undici: every outgoing fetch (the calls to payment-service, notification-service, Keycloak),
//     which also injects traceparent so the service's spans join the same trace
//   - amqplib: publishing to and consuming from RabbitMQ, with the trace context in message headers
// The outbox publisher runs later, outside the request, so insertEvent stores the request's trace
// context in the events row and the publisher restores it (lib/messaging/publisher.js).
const STARTED = Symbol.for("edemand.telemetry");

export async function startTelemetry() {
  if (globalThis[STARTED] || !process.env.OTEL_EXPORTER_OTLP_ENDPOINT) return;
  globalThis[STARTED] = true;
  const { NodeSDK } = await import("@opentelemetry/sdk-node");
  const { OTLPTraceExporter } = await import("@opentelemetry/exporter-trace-otlp-http");
  const { UndiciInstrumentation } = await import("@opentelemetry/instrumentation-undici");
  const { AmqplibInstrumentation } = await import("@opentelemetry/instrumentation-amqplib");
  new NodeSDK({
    serviceName: process.env.OTEL_SERVICE_NAME || "monolith",
    traceExporter: new OTLPTraceExporter(), // endpoint from OTEL_EXPORTER_OTLP_ENDPOINT
    instrumentations: [new UndiciInstrumentation(), new AmqplibInstrumentation()],
  }).start();
}
