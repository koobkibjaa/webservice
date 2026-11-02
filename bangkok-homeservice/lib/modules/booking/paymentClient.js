// Booking's client for payment-service, the Payments context that used to be a repository call
// inside the same transaction. Every call carries the monolith's own Keycloak token and has a
// timeout.
//
// createIntent is the call the place-booking saga depends on, so it also survives failure:
//   timeout       each attempt is abandoned after ATTEMPT_TIMEOUT_MS
//   retries       up to MAX_ATTEMPTS, on a timeout, a network error or a 5xx; never on a 4xx
//   backoff       exponential (BASE_DELAY_MS doubled each time, capped) with jitter: half the step
//                 is fixed, half random, so the retries of many callers do not arrive together,
//                 while the waits still add up to at least 3.75 s and so outlast a short outage
//   idempotency   every attempt sends the same Idempotency-Key (derived from the order id), so an
//                 attempt whose reply was lost, or that is still running when we give up on it,
//                 cannot create a second intent: payment-service replays the first one's reply
import { config } from "../../config.js";
import { logger } from "../../logger.js";
import { serviceToken } from "../identity/keycloak.js";

const ATTEMPT_TIMEOUT_MS = 2000;
const MAX_ATTEMPTS = 5;
const BASE_DELAY_MS = 500;
const MAX_DELAY_MS = 4000;

async function call(method, path, body, headers = {}) {
  const res = await fetch(`${config.paymentServiceUrl}${path}`, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${await serviceToken()}`, ...headers },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
  });
  const text = await res.text();
  let parsed = null;
  try { parsed = JSON.parse(text); } catch { /* a non-JSON body, e.g. a bare 500 */ }
  return { status: res.status, body: parsed, headers: res.headers };
}

// Wait before attempt n+1: half of BASE*2^n fixed, half random ("equal jitter").
export function backoffDelay(attempt) {
  const step = Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt);
  return step / 2 + Math.random() * (step / 2);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function createIntent(order) {
  const idempotencyKey = `place-booking-${order.id}`;
  const body = {
    order_id: order.id,
    customer_id: order.customer_id,
    provider_id: order.provider_id,
    amount_cents: order.total_cents,
  };
  let lastError;
  for (let attempt = 0; attempt < MAX_ATTEMPTS; attempt++) {
    const started = Date.now();
    let reason;
    try {
      const res = await call("POST", "/internal/payment-intents", body, { "idempotency-key": idempotencyKey });
      if (res.status === 200 || res.status === 201) {
        logger.info({
          orderId: order.id, attempt: attempt + 1, status: res.status, ms: Date.now() - started,
          replayed: res.headers.get("idempotent-replayed") === "true", idempotencyKey,
        }, "payment-service call succeeded");
        return res.body;
      }
      if (res.status < 500) {
        // A 4xx will not change on retry.
        throw Object.assign(new Error(`payment-service ${res.status}: ${res.body?.error ?? ""}`), { final: true });
      }
      reason = `HTTP ${res.status}`;
    } catch (err) {
      if (err.final) throw err;
      reason = err.name === "TimeoutError" ? `timeout after ${ATTEMPT_TIMEOUT_MS} ms` : err.message;
    }
    lastError = new Error(`payment-service: ${reason}`);
    const last = attempt === MAX_ATTEMPTS - 1;
    const delay = last ? 0 : Math.round(backoffDelay(attempt));
    logger.warn({
      orderId: order.id, attempt: attempt + 1, of: MAX_ATTEMPTS, reason, ms: Date.now() - started,
      retryInMs: last ? null : delay, idempotencyKey,
    }, last ? "payment-service call failed, giving up" : "payment-service call failed, retrying");
    if (!last) await sleep(delay);
  }
  throw lastError;
}

// For the booking detail. null when there is none, or payment-service cannot be reached.
export async function findIntent(orderId) {
  try {
    const res = await call("GET", `/internal/payment-intents/${orderId}`);
    return res.status === 200 ? res.body : null;
  } catch {
    return null;
  }
}
