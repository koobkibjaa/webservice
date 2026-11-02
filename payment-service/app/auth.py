# Who is calling. Same checks as notification-service (signature against Keycloak's JWKS, exp,
# iss, aud), with two kinds of caller:
#   a person   - a customer or provider; app_user_id/app_role claims say which monolith account
#   the monolith itself - its Keycloak service account (client-credentials grant), which alone
#                holds the internal-caller realm role; only it may use /internal/*
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


def _claims(request: Request):
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
        return jwt.decode(
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


def get_user(request: Request, role: str):
    """A person with the given role. Returns their monolith account id."""
    claims = _claims(request)
    user_role, user_id = claims.get("app_role"), claims.get("app_user_id")
    if user_role not in ("customer", "provider", "admin") or not isinstance(user_id, int):
        raise ApiError(401, "token carries no homeservice identity")
    if user_role != role:
        raise ApiError(403, f"this route requires role: {role}")
    return user_id


def require_internal(request: Request):
    """The monolith's service account, and nothing else."""
    claims = _claims(request)
    if config.INTERNAL_ROLE not in (claims.get("roles") or []):
        raise ApiError(403, "internal route")
