# Every setting comes from the environment, read once at start. docker-compose.yml declares a
# working value for each one so a clean clone boots without a .env.
import os

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://notifications:notifications-dev@localhost:5432/notifications"
)
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://edemand:edemand-dev@localhost:5672/")

# Area B: Keycloak-issued tokens, checked against Keycloak's public keys (see auth.py).
KEYCLOAK_ISSUER = os.environ.get("KEYCLOAK_ISSUER", "http://localhost:8180/realms/homeservice")
KEYCLOAK_JWKS_URL = os.environ.get(
    "KEYCLOAK_JWKS_URL", "http://localhost:8180/realms/homeservice/protocol/openid-connect/certs"
)
TOKEN_AUDIENCE = os.environ.get("TOKEN_AUDIENCE", "notification-service")

EXCHANGE = "edemand.events"
QUEUE = "notification-service.events"
DEAD_LETTER_EXCHANGE = "notification-service.dlx"
DEAD_LETTER_QUEUE = "notification-service.events.dead"
