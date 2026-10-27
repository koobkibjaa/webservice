# Every setting comes from the environment, read once at start. docker-compose.yml declares a
# working value for each one so a clean clone boots without a .env.
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://notifications:notifications-dev@localhost:5432/notifications"
)
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://edemand:edemand-dev@localhost:5672/")

# Area A: the monolith's own HS256 JWT, verified with the shared secret. Area B replaces this
# with Keycloak-issued tokens checked against Keycloak's public keys.
JWT_SECRET = os.environ.get("JWT_SECRET", "dev secret - do not use in production")

EXCHANGE = "edemand.events"
QUEUE = "notification-service.events"
DEAD_LETTER_EXCHANGE = "notification-service.dlx"
DEAD_LETTER_QUEUE = "notification-service.events.dead"
