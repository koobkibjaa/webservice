# Who is calling. The token arrives the same two ways the monolith accepts it: a Bearer header
# (API clients, the mobile app, server-side fetches) or the browser's httpOnly edemand_token cookie
# (forwarded by the gateway). Area B swaps the shared-secret check for Keycloak.
import jwt
from fastapi import Request

from . import config
from .errors import ApiError


def get_auth(request: Request):
    token = None
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer ") and header[7:].strip():
        token = header[7:].strip()
    if not token:
        token = request.cookies.get("edemand_token")
    if not token:
        raise ApiError(401, "missing bearer token")
    try:
        # The monolith signs `sub` as a number; RFC 7519 (and PyJWT >= 2.10) wants a string. Accept
        # the monolith's shape until Area B moves token issuing to Keycloak.
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"], options={"verify_sub": False})
    except jwt.PyJWTError as err:
        raise ApiError(401, f"invalid token: {err}")
    return {"sub": int(payload["sub"]), "role": payload["role"]}
