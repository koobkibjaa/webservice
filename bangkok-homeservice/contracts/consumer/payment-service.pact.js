// Consumer-driven contract: the monolith (consumer) -> payment-service (provider), the HTTP seam
// the place-booking saga crosses. Runs the monolith's real client (lib/modules/booking/
// paymentClient.js) against a Pact mock server, and writes what it sent and what it relied on to
// pacts/monolith-payment-service.json. contracts/verify.js replays that file against the real
// payment-service. Run by contracts/run.sh (docker compose run --rm contract-tests).
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { PactV3, MatchersV3 } from "@pact-foundation/pact";

const { integer, regex, string } = MatchersV3;
const PORT = 8991;
process.env.NODE_ENV = "test";
process.env.PAYMENT_SERVICE_URL = `http://127.0.0.1:${PORT}`;
const { createIntent, findIntent } = await import("../../lib/modules/booking/paymentClient.js");

const pact = new PactV3({
  consumer: "monolith",
  provider: "payment-service",
  dir: process.env.PACT_DIR || path.resolve("pacts"),
  port: PORT,
  logLevel: "warn",
});

const BEARER = regex(/^Bearer .+$/, "Bearer test-service-token");
const INTENT = (orderId) => ({
  id: integer(1),
  order_id: orderId,
  amount_cents: 64200,
  gateway_ref: regex(/^SIM-PI-.+$/, "SIM-PI-4b8e1c7a-0d55-4c55-9e7c-2a1f6c0b9d11"),
  status: string("requires_payment"),
});

test("create the payment intent for a gateway booking (saga step 2)", async () => {
  pact
    .given("no payment intent exists for order 990001")
    .uponReceiving("a request to create the payment intent for order 990001")
    .withRequest({
      method: "POST",
      path: "/internal/payment-intents",
      headers: { authorization: BEARER, "idempotency-key": "place-booking-990001", "content-type": "application/json" },
      body: { order_id: 990001, customer_id: 1, provider_id: 1, amount_cents: 64200 },
    })
    .willRespondWith({ status: 201, headers: { "content-type": "application/json" }, body: INTENT(990001) });

  await pact.executeTest(async () => {
    const intent = await createIntent({ id: 990001, customer_id: 1, provider_id: 1, total_cents: 64200 });
    assert.equal(intent.order_id, 990001);
    assert.match(intent.gateway_ref, /^SIM-PI-/);
    assert.equal(intent.status, "requires_payment");
  });
});

test("read back the payment intent for the booking detail", async () => {
  pact
    .given("order 990002 has a payment intent")
    .uponReceiving("a request for the payment intent of order 990002")
    .withRequest({ method: "GET", path: "/internal/payment-intents/990002", headers: { authorization: BEARER } })
    .willRespondWith({ status: 200, headers: { "content-type": "application/json" }, body: INTENT(990002) });

  await pact.executeTest(async () => {
    const intent = await findIntent(990002);
    assert.equal(intent.order_id, 990002);
    assert.equal(intent.amount_cents, 64200);
  });
});
