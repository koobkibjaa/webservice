// Booking's client for payment-service, the Payments context that used to be a repository call
// inside the same transaction. Every call carries the monolith's own Keycloak token and has a
// timeout. (Retries with backoff come with the failure work in Area D.)
import { config } from "../../config.js";
import { serviceToken } from "../identity/keycloak.js";

const TIMEOUT_MS = 3000;

async function call(method, path, body) {
  const res = await fetch(`${config.paymentServiceUrl}${path}`, {
    method,
    headers: { "content-type": "application/json", authorization: `Bearer ${await serviceToken()}` },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  return { status: res.status, body: await res.json() };
}

// Idempotent on order_id at payment-service: asking again for the same order returns the same
// intent, so a repeat after a lost reply is safe. Throws on anything but 200/201.
export async function createIntent(order) {
  const res = await call("POST", "/internal/payment-intents", {
    order_id: order.id,
    customer_id: order.customer_id,
    provider_id: order.provider_id,
    amount_cents: order.total_cents,
  });
  if (res.status !== 200 && res.status !== 201) {
    throw new Error(`payment-service ${res.status}: ${res.body.error ?? ""}`);
  }
  return res.body;
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
