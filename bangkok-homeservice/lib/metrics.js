// Prometheus metrics for the monolith (Area E): one histogram, the same name and labels as the
// extracted services use, so one Grafana dashboard shows all three side by side.
//   http_request_duration_seconds{service, method, route, status}
// Request rate, error rate and response time all come from it. Recorded by lib/http/handle.js for
// every API route; served on its own port (METRICS_PORT, 9464) that the gateway does not route,
// for Prometheus to scrape inside the network.
//
// Kept on globalThis: Next bundles route handlers and instrumentation.js separately, and all of
// them must record into the one registry the metrics server reads.
import http from "node:http";
import client from "prom-client";

const KEY = Symbol.for("edemand.metrics");

function metrics() {
  if (!globalThis[KEY]) {
    const registry = new client.Registry();
    const duration = new client.Histogram({
      name: "http_request_duration_seconds",
      help: "HTTP requests by route and status, and how long they took",
      labelNames: ["service", "method", "route", "status"],
      buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10],
      registers: [registry],
    });
    globalThis[KEY] = { registry, duration };
  }
  return globalThis[KEY];
}

export function observeRequest(method, route, status, seconds) {
  metrics().duration.observe({ service: "monolith", method, route, status: String(status) }, seconds);
}

export function startMetricsServer() {
  const port = Number(process.env.METRICS_PORT || 0);
  if (!port) return;
  const { registry } = metrics();
  http
    .createServer(async (req, res) => {
      res.setHeader("content-type", registry.contentType);
      res.end(await registry.metrics());
    })
    .listen(port);
}
