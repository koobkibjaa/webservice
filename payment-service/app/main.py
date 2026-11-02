# payment-service: the Payments & Wallet context, extracted from the bangkok-homeservice monolith.
#
# Public routes (nginx sends them here, at the addresses the monolith used to serve):
#   POST /api/payments/webhook       the payment gateway's callback (shared-secret header)
#   POST /api/payments/simulate      the customer's "Pay now (simulate)" button
#   GET  /api/provider/wallet        a provider's balance, commission owed and transactions
#   GET  /api/customer/transactions  a customer's transactions
# Internal routes (not routed by the gateway; only the monolith's service account may call them):
#   POST /internal/payment-intents             create the intent for a gateway booking
#                                              (Idempotency-Key required; fault switches apply)
#   GET  /internal/payment-intents/{order_id}  read it back for the booking detail
# /faults (GET, POST, POST /reset): the Area D fault switches, in-network only.
import json
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse

from . import config, consumer, db, faults, publisher
from .auth import get_user, require_internal
from .errors import ApiError

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("payment-service")


@asynccontextmanager
async def lifespan(app):
    db.init()
    consumer.start()
    publisher.start()
    yield


app = FastAPI(title="payment-service", lifespan=lifespan)


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


async def _body(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise ApiError(400, "invalid JSON body")
    if not isinstance(body, dict):
        raise ApiError(400, "invalid JSON body")
    return body


def _positive_int(value, name):
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ApiError(400, f"invalid {name}")
    return value


def _text(body, name):
    value = body.get(name)
    if not isinstance(value, str) or not value:
        raise ApiError(400, f"{name} is required")
    return value


@app.get("/health")
def health():
    db.ping()
    return {"status": "ok", "consumer": "connected" if consumer.state["connected"] else "reconnecting"}


# ---------- public ----------

@app.post("/api/payments/webhook")
async def webhook(request: Request):
    # Server-to-server from the payment gateway: authenticated by its shared secret, not a token.
    if request.headers.get("x-webhook-secret") != config.WEBHOOK_SECRET:
        raise ApiError(401, "invalid webhook secret")
    body = await _body(request)
    # On a worker thread, so concurrent callbacks really run in parallel against Postgres; the
    # transaction in db.handle_webhook, not the event loop, is what keeps them apart.
    result = await run_in_threadpool(
        db.handle_webhook, _text(body, "gateway_event_id"), _text(body, "type"), _text(body, "gateway_ref"), body
    )
    return {"received": True, **result}


@app.post("/api/payments/simulate")
async def simulate(request: Request):
    # The mock gateway's "Pay now": mints a gateway event id and drives the same idempotent webhook
    # path, so a double click credits once.
    customer_id = get_user(request, "customer")
    order_id = _positive_int((await _body(request)).get("order_id"), "order_id")
    intent = await run_in_threadpool(db.find_intent_by_order, order_id)
    if intent is None:
        raise ApiError(404, f"booking {order_id} has no gateway payment")
    if intent["customer_id"] != customer_id:  # object-level authorization
        raise ApiError(403, "you are not the customer on this booking")
    payload = {"gateway_event_id": f"SIM-EVT-{uuid.uuid4()}", "type": "payment_intent.succeeded",
               "gateway_ref": intent["gateway_ref"]}
    result = await run_in_threadpool(
        db.handle_webhook, payload["gateway_event_id"], payload["type"], payload["gateway_ref"], payload
    )
    return {"ok": True, **result}


@app.get("/api/provider/wallet")
def provider_wallet(request: Request):
    return db.wallet(get_user(request, "provider"))


@app.get("/api/customer/transactions")
def customer_transactions(request: Request):
    return db.customer_transactions(get_user(request, "customer"))


# ---------- internal (the monolith) ----------

@app.post("/internal/payment-intents")
async def create_intent(request: Request):
    require_internal(request)
    key = request.headers.get("idempotency-key", "").strip()
    if not key:
        raise ApiError(400, "Idempotency-Key header is required")
    body = await _body(request)
    await faults.before_write()
    status, intent, replayed = await run_in_threadpool(
        db.create_intent, key,
        _positive_int(body.get("order_id"), "order_id"),
        _positive_int(body.get("customer_id"), "customer_id"),
        _positive_int(body.get("provider_id"), "provider_id"),
        _positive_int(body.get("amount_cents"), "amount_cents"),
    )
    log.info(json.dumps({"msg": "idempotent replay" if replayed else "payment intent saved",
                         "order_id": intent["order_id"], "idempotency_key": key,
                         "requestId": request.state.request_id}))
    faults.after_write()
    return JSONResponse(intent, status_code=status, headers={"Idempotent-Replayed": str(replayed).lower()})


# ---------- fault switches (Area D; in-network only, see faults.py) ----------

@app.get("/faults")
def get_faults():
    return faults.snapshot()


@app.post("/faults")
async def set_faults(request: Request):
    return faults.configure(await _body(request))


@app.post("/faults/reset")
def reset_faults():
    return faults.reset()


@app.exception_handler(faults.LostReply)
async def lost_reply(request: Request, err: faults.LostReply):
    # The write is committed; the caller just never hears about it.
    return PlainTextResponse("Internal Server Error", status_code=500)


@app.get("/internal/payment-intents/{order_id}")
def get_intent(order_id: str, request: Request):
    require_internal(request)
    intent = db.find_intent_by_order(_positive_int(order_id, "order_id"))
    if intent is None:
        raise ApiError(404, f"no payment intent for order {order_id}")
    return db.public_intent(intent)
