# Who is calling. Every request must carry an access token issued by Keycloak, and this service
# checks it itself — the gateway only forwards it. Four checks, each a 401 on failure:
#   signature  RS256, against Keycloak's public keys (JWKS), so a forged or edited token fails
#   expiry     exp is in the past
#   issuer     iss is our realm, not some other Keycloak
#   audience   aud names this service, so a token minted for another API is refused here
# The token arrives as a Bearer header (mobile app, scripts, the monolith's server-side fetch) or
# as the browser's httpOnly kc_token cookie (the "Mark read" button, forwarded by the gateway).
import jwt
from fastapi import Request

from . import config
from .errors import ApiError

# Keys are fetched once and cached, so a short Keycloak outage does not stop token checks.
_jwks = jwt.PyJWKClient(config.KEYCLOAK_JWKS_URL, cache_keys=True, lifespan=300)


def _read_token(request: Request):
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer ") and header[7:].strip():
        return header[7:].strip()
    return request.cookies.get("kc_token")


def get_auth(request: Request):
    token = _read_token(request)
    if not token:
        raise ApiError(401, "missing bearer token")
    try:
        key = _jwks.get_signing_key_from_jwt(token).key
    except jwt.PyJWKClientConnectionError:
        raise ApiError(503, "identity provider unavailable")
    except jwt.PyJWTError as err:
        raise ApiError(401, f"invalid token: {err}")

    try:
        claims = jwt.decode(
            token, key, algorithms=["RS256"],
            audience=config.TOKEN_AUDIENCE, issuer=config.KEYCLOAK_ISSUER,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "token expired")
    except jwt.InvalidAudienceError:
        raise ApiError(401, f"token was not issued for {config.TOKEN_AUDIENCE}")
    except jwt.PyJWTError as err:
        raise ApiError(401, f"invalid token: {err}")

    # Which monolith account this is: Keycloak copies it from the user's attributes into the token.
    role, user_id = claims.get("app_role"), claims.get("app_user_id")
    if role not in ("customer", "provider", "admin") or not isinstance(user_id, int):
        raise ApiError(401, "token carries no homeservice identity")
    return {"sub": user_id, "role": role}
