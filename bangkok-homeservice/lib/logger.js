// Structured JSON logging (pino). One logger instance, shared everywhere, so log shape is
// consistent whether it's app bootstrap, a request handler, or the background relay.
// Every line logged inside a traced request or message also carries trace_id: the correlation id
// that is the same in this service's logs, the extracted services' logs, and Jaeger.
import pino from "pino";
import { trace } from "@opentelemetry/api";
import { config } from "./config.js";

export const logger = pino({
  level: config.logLevel,
  base: { service: "edemand" },
  timestamp: pino.stdTimeFunctions.isoTime,
  mixin() {
    const span = trace.getActiveSpan();
    return span ? { trace_id: span.spanContext().traceId } : {};
  },
});
