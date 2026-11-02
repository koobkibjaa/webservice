# DES428 capstone — bangkok-homeservice, migrated

The bangkok-homeservice monolith (Next.js + SQLite) with its **Notifications** context extracted
into its own service, behind an nginx gateway.

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
the token notification-service accepts. Someone who registers later is created in Keycloak on
their first login. To get a token straight from Keycloak, as the mobile app would:

```bash
curl -s -X POST http://localhost:8180/realms/homeservice/protocol/openid-connect/token \
  -d grant_type=password -d client_id=homeservice-mobile -d username=customer-1 \
  --data-urlencode 'password=eDemand-2026!'
```

The monolith reseeds its database on every boot; notification-service's Postgres and Keycloak keep their data
until `docker compose down -v`.

## What runs

```
browser / mobile app
        │ :8080
     gateway (nginx)
        ├── /api/notifications, /api/notifications/{id}/read ──► notification-service (FastAPI)
        │                                                          │  owns notification-db (Postgres)
        └── everything else ──► monolith (Next.js, SQLite)         │
                                  │ outbox `events` table          │
                                  └── publisher ──► RabbitMQ ──────┘ consumer (dedupes on event_id)
```

| Service | Folder | Database |
|---|---|---|
| gateway | `gateway/nginx.conf` | — |
| monolith | `bangkok-homeservice/` | SQLite inside the container |
| notification-service | `notification-service/` | `notification-db` (Postgres 16) |
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
