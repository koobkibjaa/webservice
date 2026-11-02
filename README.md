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

Expected output (request ids and notification ids differ), exit code 0:

```
  PASS  valid token (Nong)                           200  [{"id":4,"type":"booking_status",...
  PASS  no token                                     401  {"error":"missing bearer token",...
  PASS  forged token (payload edited)                401  {"error":"invalid token: Signature verification failed",...
  PASS  expired token                                401  {"error":"token expired",...
  PASS  wrong-audience token                         401  {"error":"token was not issued for notification-service",...
  PASS  Somchai marks Nong's notification #4 read    403  {"error":"not your notification",...
  PASS  Nong marks her own notification #4 read      200  {"id":4,...

All checks passed.
```

notification-service checks every token itself (`notification-service/app/auth.py`): the RS256
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

## Tests

```bash
cd bangkok-homeservice && docker compose run --rm --build test    # monolith suite, in-memory
```
