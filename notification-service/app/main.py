# notification-service: the Notifications context, extracted from the bangkok-homeservice monolith.
# It serves the same two routes the monolith served, at the same addresses (nginx routes them
# here), and builds its data from domain events it receives through RabbitMQ.
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import consumer, db, telemetry
from .auth import get_auth
from .errors import ApiError

telemetry.setup_logging()
log = logging.getLogger("notification-service")


@asynccontextmanager
async def lifespan(app):
    db.init()
    consumer.start()
    yield


app = FastAPI(title="notification-service", lifespan=lifespan)
telemetry.setup_tracing(app)  # before the pool opens and the RabbitMQ threads start
telemetry.setup_metrics(app)


@app.middleware("http")
async def request_id(request: Request, call_next):
    rid = request.headers.get("x-request-id") or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    return response


@app.exception_handler(ApiError)
async def api_error(request: Request, err: ApiError):
    rid = request.state.request_id
    log.warning(json.dumps({"msg": "request error", "status": err.status, "err": err.message, "requestId": rid}))
    return JSONResponse({"error": err.message, "requestId": rid}, status_code=err.status)


@app.get("/health")
def health():
    db.ping()
    return {"status": "ok", "consumer": "connected" if consumer.state["connected"] else "reconnecting"}


@app.get("/api/notifications")
def list_notifications(request: Request):
    auth = get_auth(request)
    return db.list_for_recipient(auth["role"], auth["sub"])


@app.post("/api/notifications/{notification_id}/read")
def mark_read(notification_id: str, request: Request):
    auth = get_auth(request)
    if not notification_id.isdigit() or int(notification_id) <= 0:
        raise ApiError(400, "invalid notification id")
    nid = int(notification_id)
    owner = db.find_owner(nid)
    if owner is None:
        raise ApiError(404, f"notification {nid} not found")
    # Object-level authorization: you may only touch your own notification.
    if owner[0] != auth["role"] or owner[1] != auth["sub"]:
        raise ApiError(403, "not your notification")
    return db.mark_read(nid, auth["role"], auth["sub"])
