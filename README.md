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

**Seeded logins** (password for all: `eDemand-2026!`): customers `nong@example.com`,
`somchai@example.com`, `aom@example.com`; providers `sparkle@example.com`, `fixit@example.com`
(`glow@example.com` is pending approval); admin `admin@edemand.test`.

The monolith reseeds its database on every boot; notification-service's Postgres keeps its data
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

## Tests

```bash
cd bangkok-homeservice && docker compose run --rm --build test    # monolith suite, in-memory
```
