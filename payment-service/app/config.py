# Every setting comes from the environment, read once at start. docker-compose.yml declares a
# working value for each one so a clean clone boots without a .env.
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://payments:payments-dev@localhost:5432/payments")
RABBITMQ_URL = os.environ.get("RABBITMQ_URL", "amqp://edemand:edemand-dev@localhost:5672/")

# Keycloak-issued tokens, checked against Keycloak's public keys (see auth.py).
KEYCLOAK_ISSUER = os.environ.get("KEYCLOAK_ISSUER", "http://localhost:8180/realms/homeservice")
KEYCLOAK_JWKS_URL = os.environ.get(
    "KEYCLOAK_JWKS_URL", "http://localhost:8180/realms/homeservice/protocol/openid-connect/certs"
)
TOKEN_AUDIENCE = os.environ.get("TOKEN_AUDIENCE", "payment-service")
# The realm role only the monolith's service account holds; required on /internal/* routes.
INTERNAL_ROLE = "internal-caller"

# The payment gateway signs its callbacks with this shared secret (it cannot hold a Keycloak token).
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "edemand-webhook-secret")

EXCHANGE = "edemand.events"
QUEUE = "payment-service.events"
DEAD_LETTER_EXCHANGE = "payment-service.dlx"
DEAD_LETTER_QUEUE = "payment-service.events.dead"
