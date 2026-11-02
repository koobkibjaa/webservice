// The single wrapper every route handler is built with. It derives a request id, runs the handler,
// stamps X-Request-Id on whatever response comes back, and funnels any thrown error through
// toResponse so status + body + logging stay uniform. This is the App Router analogue of Ride's
// requestId + errorHandler middleware pair. It also records the request's duration and status
// for Prometheus (lib/metrics.js).
import { requestId } from "./requestId.js";
import { toResponse } from "./respond.js";
import { observeRequest } from "../metrics.js";

// /api/bookings/8001/confirm -> /api/bookings/:id/confirm, so ids do not become metric labels.
function routeOf(req) {
  return new URL(req.url, "http://x").pathname.replace(/\/\d+(?=\/|$)/g, "/:id");
}

export function handle(fn) {
  return async (req, ctx = {}) => {
    const id = requestId(req);
    const started = performance.now();
    let res;
    try {
      res = await fn(req, ctx, id);
      if (res && res.headers && !res.headers.get("X-Request-Id")) {
        res.headers.set("X-Request-Id", id);
      }
    } catch (err) {
      res = toResponse(err, id);
    }
    observeRequest(req.method, routeOf(req), res?.status ?? 500, (performance.now() - started) / 1000);
    return res;
  };
}
