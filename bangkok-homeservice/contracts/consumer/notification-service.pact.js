// Consumer-driven contract: the monolith (consumer) -> notification-service (provider). The
// monolith's /notifications page reads the signed-in user's list over HTTP with their Keycloak
// token (lib/http/remote.js). This test pins down the fields the page uses (title, type, body,
// read_at, id for the "Mark read" button) and writes pacts/monolith-notification-service.json for
// contracts/verify.js to replay against the real notification-service.
import { test } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import { PactV3, MatchersV3 } from "@pact-foundation/pact";

const { integer, regex, string, eachLike } = MatchersV3;
const PORT = 8992;
process.env.NODE_ENV = "test";
const { fetchJson } = await import("../../lib/http/remote.js");

const pact = new PactV3({
  consumer: "monolith",
  provider: "notification-service",
  dir: process.env.PACT_DIR || path.resolve("pacts"),
  port: PORT,
  logLevel: "warn",
});

test("the signed-in customer's notifications, newest first", async () => {
  pact
    .given("customer 990001 has one unread notification")
    .uponReceiving("a request for customer 990001's notifications")
    .withRequest({
      method: "GET",
      path: "/api/notifications",
      headers: { authorization: regex(/^Bearer .+$/, "Bearer user-token"), accept: "application/json" },
    })
    .willRespondWith({
      status: 200,
      headers: { "content-type": "application/json" },
      body: eachLike({
        id: integer(1),
        type: string("booking_status"),
        title: string("Booking confirmed"),
        body: string("Booking #8001 is now confirmed."),
        order_id: integer(8001),
        read_at: null,
        created_at: regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/, "2026-11-02T14:38:47.123Z"),
      }),
    });

  await pact.executeTest(async (mock) => {
    const list = await fetchJson(`${mock.url}/api/notifications`, "user-token");
    assert.ok(Array.isArray(list) && list.length >= 1);
    assert.equal(list[0].title, "Booking confirmed");
    assert.equal(list[0].read_at, null, "unread: the page shows the Mark read button");
  });
});
