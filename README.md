# DES428 capstone — bangkok-homeservice, migrated

The bangkok-homeservice monolith (Next.js + SQLite) with two contexts extracted into their own
services behind an nginx gateway: **Notifications** (read side, fed by events) and **Payments &
Wallet** (moves money; a saga with the monolith's Booking).

## Run it

```bash
docker compose up -d --build --wait        # the one command
docker compose ps                          # every service should say (healthy)
```

Reset to first boot (removes containers, network and data volumes):

```bash
docker compose down -v --remove-orphans
```

| Screen | Address |
|---|---|
| The app (web pages + API, through the gateway) | http://localhost:8080 |
| RabbitMQ management | http://localhost:15672 (`edemand` / `edemand-dev`) |
| Keycloak admin console | http://localhost:8180 (`admin` / `admin-dev`), realm `homeservice` |
| Jaeger (traces) | http://localhost:16686 |
| Grafana (dashboard "Services: rate, errors, response time") | http://localhost:3000 (opens without login; `admin` / `admin-dev` to edit) |
| Prometheus | http://localhost:9090 |

**Seeded logins** (password for all: `eDemand-2026!`):

| Who | Monolith login (email) | Keycloak username |
|---|---|---|
| Nong, Somchai, Aom (customers 1–3) | `nong@` / `somchai@` / `aom@example.com` | `customer-1` / `customer-2` / `customer-3` |
| Sparkle, FixIt, Glow (providers 1–3; Glow pending approval) | `sparkle@` / `fixit@` / `glow@example.com` | `provider-1` / `provider-2` / `provider-3` |
| Ops Admin | `admin@edemand.test` | `admin-1` |

Logging in to the monolith also returns a `keycloak_token` (and sets a `kc_token` cookie): that is
the token notification-service and payment-service accept. Someone who registers later is created in Keycloak on
their first login. To get a token straight from Keycloak, as the mobile app would:

```bash
curl -s -X POST http://localhost:8180/realms/homeservice/protocol/openid-connect/token \
  -d grant_type=password -d client_id=homeservice-mobile -d username=customer-1 \
  --data-urlencode 'password=eDemand-2026!'
```

Every database (the monolith's SQLite file, both services' Postgres, Keycloak's) keeps its data
across restarts and is reset together by `docker compose down -v`. The monolith seeds only an
empty database, so after a reset the first booking is again #8001.

## What runs

```
browser / mobile app
        │ :8080
     gateway (nginx)
        ├── /api/notifications*                      ──► notification-service (FastAPI) ── notification-db
        ├── /api/payments/{webhook,simulate},
        │   /api/provider/wallet,
        │   /api/customer/transactions               ──► payment-service (FastAPI) ─────── payment-db
        └── everything else ──► monolith (Next.js, SQLite)            ▲ HTTP /internal/payment-intents
                                  │  └─────────────────────────────────┘ (place-booking saga, step 2)
                                  │
            monolith outbox ──► RabbitMQ ──► notification-service: every booking/review/payment event
                                         ──► payment-service:      booking.completed, booking.cancelled
            payment-service outbox ──► RabbitMQ ──► monolith:      payment.succeeded (order marked paid)
```

Every consumer dedupes on the event's `event_id` in the same transaction as its effect, so a
redelivered event changes nothing.

| Service | Folder | Database |
|---|---|---|
| gateway | `gateway/nginx.conf` | — |
| monolith | `bangkok-homeservice/` | SQLite file in the `monolith-data` volume |
| notification-service | `notification-service/` | `notification-db` (Postgres 16) |
| payment-service | `payment-service/` | `payment-db` (Postgres 16) |
| rabbitmq | — | — |
| keycloak | `keycloak/realm-homeservice.json` (imported on first boot) | its own, inside the container |

## Area A — extract and route: how to check it

1. **Same addresses still work.** Log in at http://localhost:8080 as `nong@example.com`, make a
   booking; log in as `sparkle@example.com` and confirm it. Back as Nong, open
   *Notifications*: "Booking confirmed" is there. The page and `GET /api/notifications` are served
   by notification-service.
2. **The monolith no longer has the table.**
   ```bash
   docker compose exec monolith node -e "const D=require('better-sqlite3');console.log(new D('/app/data/edemand.db').prepare(\"select count(*) n from sqlite_master where name='notifications'\").get())"
   # { n: 0 }
   ```
   The data lives in Postgres instead:
   ```bash
   docker compose exec notification-db psql -U notifications -c "select id, recipient_role, recipient_id, type, title from notifications"
   ```
3. **A duplicate delivery makes one effect.** Re-publish an already-applied event from the
   RabbitMQ screen (exchange `edemand.events`); `docker compose logs notification-service` shows
   `duplicate event skipped` and the notification count does not change.

### Payments (the second seam, which changes data)

4. **Pay for a booking, at the same addresses.** As Nong, book with *Payment: gateway* and press
   *Pay now (simulate)*. `POST /api/payments/simulate` is served by payment-service; the
   monolith's order turns `paid` when payment-service's `payment.succeeded` event arrives.
5. **The wallet moved.** As Sparkle, confirm, start and complete that booking with Nong's OTP, then
   open *Wallet*: the earnings are there, read from payment-service.
   ```bash
   docker compose exec payment-db psql -U payments -c "select provider_id, balance_cents, commission_owed_cents from provider_wallets"
   docker compose exec monolith node -e "const D=require('better-sqlite3');console.log(new D('/app/data/edemand.db').prepare(\"select name from sqlite_master where name in ('payment_intents','transactions','provider_wallets','webhook_events')\").all())"
   # []
   ```
6. **The place-booking saga compensates.** Stop payment-service and place a gateway booking:
   ```bash
   docker compose stop payment-service
   # book with payment_method "gateway" -> 503 "payment service unavailable; the booking was not
   # placed and the slot was released"; the order is cancelled and the slot can be booked again
   docker compose start payment-service
   ```

## Area B — secure: how to check it

### Demo script: `scripts/beat3-auth-rejection.sh`

```bash
bash scripts/beat3-auth-rejection.sh
```

Expected output (request ids, notification and booking ids differ), exit code 0:

```
== notification-service, through the gateway (/api/notifications)
  PASS  valid token (Nong)                             200  [{"id":16,"type":"booking_status",...
  PASS  no token                                       401  {"error":"missing bearer token",...
  PASS  forged token (payload edited)                  401  {"error":"invalid token: Signature verification failed",...
  PASS  expired token                                  401  {"error":"token expired",...
  PASS  wrong-audience token                           401  {"error":"token was not issued for notification-service",...
  PASS  Somchai marks Nong's notification #16 read     403  {"error":"not your notification",...
  PASS  Nong marks her own notification #16 read       200  {"id":16,...

== payment-service, through the gateway (/api/customer/transactions)
  PASS  valid token (Nong)                             200  [...
  PASS  no token                                       401  {"error":"missing bearer token",...
  PASS  forged token (payload edited)                  401  {"error":"invalid token: Signature verification failed",...
  PASS  expired token                                  401  {"error":"token expired",...
  PASS  wrong-audience token                           401  {"error":"token was not issued for payment-service",...
  PASS  Somchai pays Nong's booking #8006              403  {"error":"you are not the customer on this booking",...
  PASS  Nong pays her own booking #8006                200  {"ok":true,"replayed":false,"settled":true}

All checks passed.
```

Each run books one gateway-paid slot for Nong (the first free one) for the payment ownership check.

Both services check every token themselves (`notification-service/app/auth.py`, `payment-service/app/auth.py`): the RS256
signature against Keycloak's public keys, `exp`, `iss` and `aud`. The Keycloak realm has a
3-second-token client (`expiry-demo`) and a client whose tokens are for another API
(`reporting-app`) so the expired and wrong-audience cases are real Keycloak tokens.

### The broken object-level authorization fix

`GET /api/jobs` listed only a customer's own jobs, but `GET /api/jobs/{id}` returned **any** job to
any signed-in user. Somchai (customer 2) reading Nong's job #1:

```bash
S=$(curl -s -X POST localhost:8080/api/auth/customer/login -H 'content-type: application/json' \
  -d '{"email":"somchai@example.com","password":"eDemand-2026!"}' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
curl -s -w ' [%{http_code}]\n' localhost:8080/api/jobs/1 -H "authorization: Bearer $S"
# before the fix: {"job":{"id":1,"customer_id":1,"title":"Move-out deep clean, 2BR",...} [200]
# after the fix:  {"error":"you do not own this job",...} [403]
```

Fixed in `bangkok-homeservice/lib/modules/bidding/service.js` (`getJob`); regression tests in
`bangkok-homeservice/test/bidding.test.js`.

## Area D — survive failure: how to check it

The call across the seam is the monolith's `POST /internal/payment-intents` to payment-service,
made while it places a gateway-paid booking (`bangkok-homeservice/lib/modules/booking/paymentClient.js`):
a **2 s timeout** per attempt, **up to 5 attempts** on a timeout, network error or 5xx (never on a
4xx), **exponential backoff with jitter** (500 ms doubled each time, capped at 4 s, half of each
step random), and one **`Idempotency-Key`** (`place-booking-<order id>`) on every attempt.
payment-service claims the key, writes the intent and stores the reply in one transaction
(`payment-service/app/db.py`, `create_intent`), so a retried write is saved once.

**Fault switches** (`payment-service/app/faults.py`), flags the service reads before it replies;
reachable only inside the network:

```bash
docker compose exec payment-service python -m app.faults lost_reply 1   # write, then lose the reply
docker compose exec payment-service python -m app.faults slow 1 5       # reply after 5 s (> 2 s timeout)
docker compose exec payment-service python -m app.faults outage 3       # fail every call for 3 s
docker compose exec payment-service python -m app.faults reset
docker compose exec payment-service python -m app.faults                # show the switches
```

### Demo script: `scripts/beat2-failure-recovery.sh`

```bash
bash scripts/beat2-failure-recovery.sh
```

Expected output (order ids, timings and how many retries the outage needs differ), exit code 0:

```
== Fault 1: the reply is lost after the write (lost_reply 1)
    POST /api/bookings -> HTTP 201 in 0.49s, order #8018
      attempt 1: "HTTP 500" after 9 ms -> failed, retrying
      attempt 2: 201 after 11 ms -> succeeded
    PASS  booking answer                     201
    PASS  order status in the monolith       awaiting
    PASS  payment intents for #8018          1

== Fault 2: the reply is slower than the 2 s timeout (slow 1, 5 s)
    POST /api/bookings -> HTTP 201 in 2.52s, order #8019
      attempt 1: "timeout after 2000 ms" after 2001 ms -> failed, retrying
      attempt 2: 201 after 11 ms -> succeeded
    PASS  ...                                        (201, awaiting, 1 intent)

== Fault 3: payment-service fails for a 3 s window (outage 3)
      attempt 1..3: "HTTP 503" -> failed, retrying
      attempt 4: 201 -> succeeded
    PASS  ...                                        (201, awaiting, 1 intent)

== Beyond the retries: a 30 s outage -> the saga compensates
      attempt 1..5: "HTTP 503" -> ... giving up
    PASS  booking answer                     503
    PASS  order status in the monolith       cancelled
    PASS  payment intents for #8021          0

payment-service's side (the Idempotency-Key at work):
        1 "msg": "fault: reply lost after the write"
        1 "msg": "idempotent replay", "order_id": 8018
        ...
All checks passed.
```

### The race: payment-gateway callbacks at the same instant

The README of the inherited system names the payment-webhook race ("the real product has no
idempotency and double-processes retries"). The monolith was protected by SQLite's single writer
lock; payment-service runs callbacks truly in parallel on Postgres, so its webhook holds on its own:
the event id is claimed with `INSERT ... ON CONFLICT DO NOTHING`, the intent row is locked
(`SELECT ... FOR UPDATE`) and flipped with a conditional `UPDATE`. The test releases 25 requests at
once with a barrier, twice:

```bash
bash scripts/race-webhook.sh
# burst A on #8016, 25 x the same gateway_event_id: {'replayed': 24, 'settled': 1}
# burst B on #8017, 25 x different event ids:      {'already_settled': 24, 'settled': 1}
#   PASS  A: charges for #8016                         1
#   PASS  A: payment.succeeded events for #8016        1
#   PASS  A: webhook_events rows for the repeated id   1
#   PASS  B: charges for #8017                         1
#   PASS  B: payment.succeeded events for #8017        1
#   PASS  B: requests that settled                     1
```

## Area E — observe and ship: how to check it

**Traces** (OpenTelemetry → Jaeger). The monolith (Next.js spans, `fetch`, RabbitMQ) and both
services (FastAPI, RabbitMQ, SQL) send spans to Jaeger. A trace crosses both seams: the HTTP call
to payment-service carries `traceparent`, and an outbox event keeps the trace context of the
request that wrote it, so its RabbitMQ message continues that trace in the consumer.

**Logs** are JSON in every service and carry `trace_id`, the correlation id that is the same in
all three services' logs and in Jaeger:

```bash
docker compose logs monolith payment-service notification-service | grep <trace id>
```

**Metrics** (Prometheus → Grafana). All three expose the same histogram,
`http_request_duration_seconds{service, method, route, status}`; the Grafana dashboard shows
request rate, 5xx error rate and response time (p95, p50) for each service, and a per-route view.

### Demo script: `scripts/beat4-find-the-trace.sh`

```bash
bash scripts/beat4-find-the-trace.sh
```

Places one gateway-paid booking with a `traceparent` the script chose, then asks Jaeger for that
trace. Expected output (ids and counts differ), exit code 0:

```
   booking #8025 created; trace id fab0a3e606afb6aecd37834d0bbc7c24
== Jaeger: trace fab0a3e606afb6aecd37834d0bbc7c24
   services in this one trace: monolith notification-service payment-service
   operations (count, name):
           4 INSERT
           1 publish edemand.events
           1 fetch POST http://payment-service:8000/internal/payment-intents
           1 edemand.events receive
           1 POST /internal/payment-intents
           1 POST /app/api/bookings/route
           ...
   open it: http://localhost:16686/trace/fab0a3e606afb6aecd37834d0bbc7c24
== The same id in each service's logs (trace_id = correlation id)
   monolith               "msg":"payment-service call succeeded"
   payment-service        "msg": "payment intent saved"
   notification-service   "msg": "event applied"
  PASS  monolith               is in the trace
  PASS  payment-service        is in the trace
  PASS  notification-service   is in the trace
  PASS  span "POST /internal/payment-intents"
  PASS  span "publish edemand.events"
All checks passed.
```

### Contract tests (Pact), one per new boundary

```bash
docker compose run --rm contract-tests
```

The same command runs in CI (`.github/workflows/ci.yml`, with the monolith's unit tests). The
monolith is the consumer of both new boundaries, so its consumer tests
(`bangkok-homeservice/contracts/consumer/`) run its real client code against a Pact mock and write
`pacts/`; `contracts/verify.js` then replays them against the real services, with provider states
written into their databases and real Keycloak tokens. Expected ending:

```
Verifying a pact between monolith and payment-service
  a request for the payment intent of order 990002 ... has a matching body (OK)
  a request to create the payment intent for order 990001 ... has a matching body (OK)
Verifying a pact between monolith and notification-service
  a request for customer 990001's notifications ... has a matching body (OK)
Both providers honour the monolith's contracts.
```

If payment-service stopped sending a field the monolith relies on, verification fails with
`$ -> Actual map is missing the following keys: ...` and exit code 1.

## Tests

```bash
cd bangkok-homeservice && docker compose run --rm --build test    # monolith suite, in-memory
```
