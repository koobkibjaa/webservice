// Response construction + the one place that turns any thrown error into an HTTP response.
//
// We build the web-standard global `Response` (not next/server's NextResponse — NextResponse merely
// extends Response, and App Router route handlers accept a plain Response return). Using the global
// keeps the handlers importable under plain `node --test`, which is exactly how the suite drives
// them in-process, while behaving identically inside the Next server.
//
// Every route handler throws AppError (or a subclass) on failure, so the JSON error shape is
// guaranteed consistent across all eight contexts: { error, requestId, details? }. Unexpected
// 5xx bodies are scrubbed to "internal server error"; a deliberate AppError 5xx (503) keeps its
// message. 5xx are logged at error level; 4xx at warn.
import { AppError } from "../errors.js";
import { logger } from "../logger.js";

export function json(body, { status = 200, headers = {} } = {}) {
  const res = Response.json(body, { status });
  for (const [k, v] of Object.entries(headers)) res.headers.set(k, v);
  return res;
}

function isSyntaxError(err) {
  return err instanceof SyntaxError || err?.name === "SyntaxError";
}

export function toResponse(err, requestId) {
  // A malformed JSON body (readJson threw) is a client mistake — 400, never a 500 crash.
  if (isSyntaxError(err)) {
    logger.warn({ requestId, err: err.message }, "invalid json body");
    return json({ error: "invalid JSON body", requestId }, { status: 400, headers: { "X-Request-Id": requestId } });
  }

  const statusCode = err instanceof AppError ? err.statusCode : 500;
  const body = { error: err.message || "internal server error", requestId };
  if (err instanceof AppError && err.details !== undefined) {
    body.details = err.details;
  }

  if (statusCode >= 500 && !(err instanceof AppError)) {
    logger.error({ requestId, statusCode, err: err.stack || String(err) }, "unhandled error");
    body.error = "internal server error";
    delete body.details;
  } else if (statusCode >= 500) {
    // A deliberate 5xx (e.g. 503 when payment-service is down): logged, but the message is ours
    // and safe to show.
    logger.error({ requestId, statusCode, err: err.message }, "request failed");
  } else {
    logger.warn({ requestId, statusCode, err: err.message }, "request error");
  }

  return json(body, { status: statusCode, headers: { "X-Request-Id": requestId } });
}
