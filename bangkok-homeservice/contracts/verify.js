// Provider verification: replay each pact the consumer tests wrote against the real, running
// service, and fail if any reply no longer matches what the monolith relies on.
//   pacts/monolith-payment-service.json       -> payment-service
//   pacts/monolith-notification-service.json  -> notification-service
// Provider states put the data each interaction assumes straight into the provider's database.
// The request filter swaps the placeholder token for a real Keycloak token, because the services
// check every token themselves (the monolith's service account for /internal/*, the pact test
// customer 990001 for the notifications list).
import path from "node:path";
import { Verifier } from "@pact-foundation/pact";
import pg from "pg";

const PACT_DIR = process.env.PACT_DIR || path.resolve("pacts");
const KEYCLOAK = `${process.env.KEYCLOAK_URL}/realms/homeservice/protocol/openid-connect/token`;

async function keycloakToken(params) {
  const res = await fetch(KEYCLOAK, { method: "POST", body: new URLSearchParams(params) });
  const body = await res.json();
  if (!res.ok) throw new Error(`Keycloak ${res.status}: ${body.error}`);
  return body.access_token;
}

async function sql(url, text, values = []) {
  const client = new pg.Client({ connectionString: url });
  await client.connect();
  try {
    await client.query(text, values);
  } finally {
    await client.end();
  }
}

function withToken(token) {
  return (req, _res, next) => {
    req.headers.authorization = `Bearer ${token}`;
    next();
  };
}

const serviceToken = await keycloakToken({
  grant_type: "client_credentials",
  client_id: "homeservice-monolith",
  client_secret: process.env.KEYCLOAK_CLIENT_SECRET,
});
const customerToken = await keycloakToken({
  grant_type: "password",
  client_id: "homeservice-mobile",
  username: "customer-990001",
  password: process.env.PACT_CUSTOMER_PASSWORD,
});

const PAYMENT_DB = process.env.PAYMENT_DB_URL;
const NOTIFICATION_DB = process.env.NOTIFICATION_DB_URL;

await new Verifier({
  provider: "payment-service",
  providerBaseUrl: process.env.PAYMENT_SERVICE_URL,
  pactUrls: [path.join(PACT_DIR, "monolith-payment-service.json")],
  requestFilter: withToken(serviceToken),
  stateHandlers: {
    "no payment intent exists for order 990001": async () => {
      await sql(PAYMENT_DB, "DELETE FROM payment_intents WHERE order_id = 990001");
      await sql(PAYMENT_DB, "DELETE FROM idempotency_keys WHERE key = 'place-booking-990001'");
    },
    "order 990002 has a payment intent": async () => {
      await sql(PAYMENT_DB,
        `INSERT INTO payment_intents (order_id, customer_id, provider_id, amount_cents, gateway_ref)
         VALUES (990002, 1, 1, 64200, 'SIM-PI-pact-990002') ON CONFLICT (order_id) DO NOTHING`);
    },
  },
  logLevel: "warn",
}).verifyProvider();

await new Verifier({
  provider: "notification-service",
  providerBaseUrl: process.env.NOTIFICATION_SERVICE_URL,
  pactUrls: [path.join(PACT_DIR, "monolith-notification-service.json")],
  requestFilter: withToken(customerToken),
  stateHandlers: {
    "customer 990001 has one unread notification": async () => {
      await sql(NOTIFICATION_DB, "DELETE FROM notifications WHERE recipient_role = 'customer' AND recipient_id = 990001");
      await sql(NOTIFICATION_DB,
        `INSERT INTO notifications (recipient_role, recipient_id, type, title, body, order_id)
         VALUES ('customer', 990001, 'booking_status', 'Booking confirmed', 'Booking #8001 is now confirmed.', 8001)`);
    },
  },
  logLevel: "warn",
}).verifyProvider();

console.log("Both providers honour the monolith's contracts.");
