# Fault switches (Area D), built the way the week-11 lab built slow_calls: a flag the service reads
# before it replies. They act on POST /internal/payment-intents, the call the monolith makes across
# the seam while it places a gateway booking:
#   lost_reply  N  the next N calls do the write, commit it, then lose the reply (the caller sees a
#                  500, exactly as if the answer had been lost on the way back)
#   slow        N  the next N calls wait slow_seconds before doing anything, longer than the
#                  caller's timeout
#   outage      S  for the next S seconds every call fails at once with 503
# The switches live in this process's memory and are set through /faults, which the gateway does
# not route: only something inside the network (docker compose exec) can reach it.
#
#   docker compose exec payment-service python -m app.faults lost_reply 1
#   docker compose exec payment-service python -m app.faults slow 1 [seconds]
#   docker compose exec payment-service python -m app.faults outage 3
#   docker compose exec payment-service python -m app.faults reset
import asyncio
import json
import logging
import sys
import time
import urllib.request

from .errors import ApiError

log = logging.getLogger("payment-service.faults")
state = {"lost_reply": 0, "slow": 0, "slow_seconds": 5.0, "outage_until": 0.0}


class LostReply(Exception):
    """Raised after the write has committed, so the reply never reaches the caller."""


def snapshot():
    return {
        "lost_reply": state["lost_reply"],
        "slow": state["slow"],
        "slow_seconds": state["slow_seconds"],
        "outage_seconds_left": max(0.0, round(state["outage_until"] - time.time(), 1)),
    }


def configure(body):
    if "lost_reply" in body:
        state["lost_reply"] = int(body["lost_reply"])
    if "slow" in body:
        state["slow"] = int(body["slow"])
    if "slow_seconds" in body:
        state["slow_seconds"] = float(body["slow_seconds"])
    if "outage_seconds" in body:
        state["outage_until"] = time.time() + float(body["outage_seconds"])
    log.warning(json.dumps({"msg": "fault switches set", **snapshot()}))
    return snapshot()


def reset():
    state.update(lost_reply=0, slow=0, slow_seconds=5.0, outage_until=0.0)
    return snapshot()


async def before_write():
    if time.time() < state["outage_until"]:
        log.warning(json.dumps({"msg": "fault: outage, failing the call"}))
        raise ApiError(503, "payment-service unavailable (fault: outage)")
    if state["slow"] > 0:
        state["slow"] -= 1
        log.warning(json.dumps({"msg": "fault: slow reply", "seconds": state["slow_seconds"]}))
        await asyncio.sleep(state["slow_seconds"])


def after_write():
    if state["lost_reply"] > 0:
        state["lost_reply"] -= 1
        log.warning(json.dumps({"msg": "fault: reply lost after the write"}))
        raise LostReply()


def _cli(argv):
    url = "http://127.0.0.1:8000/faults"
    if argv[:1] == ["reset"]:
        url, body = url + "/reset", {}
    elif argv[:1] == ["lost_reply"]:
        body = {"lost_reply": int(argv[1]) if len(argv) > 1 else 1}
    elif argv[:1] == ["slow"]:
        body = {"slow": int(argv[1]) if len(argv) > 1 else 1}
        if len(argv) > 2:
            body["slow_seconds"] = float(argv[2])
    elif argv[:1] == ["outage"]:
        body = {"outage_seconds": float(argv[1]) if len(argv) > 1 else 3}
    else:
        print(urllib.request.urlopen(url).read().decode())
        return
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    print(urllib.request.urlopen(req).read().decode())


if __name__ == "__main__":
    _cli(sys.argv[1:])
