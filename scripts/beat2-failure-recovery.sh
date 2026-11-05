#!/usr/bin/env bash
# Beat 2 — a fault is survived with exactly one effect.
#
# Placing a gateway-paid booking makes the monolith call payment-service across the seam
# (POST /internal/payment-intents). That call has a 2 s timeout, up to 5 attempts with exponential
# backoff and jitter, and one Idempotency-Key for all attempts. For each of payment-service's three
# fault switches this script turns the switch on, places a booking through the gateway, and shows:
# the booking succeeded, the monolith's attempts, and that exactly one payment intent exists.
# A fourth case shows an outage longer than the retries: the saga gives up and compensates.
# Needs bash, curl and docker compose (run from the repository root). Exit code 0 = all as expected.
set -uo pipefail
# A date N days from today: GNU date (Linux, Git Bash) or BSD date (macOS).
days_ahead() { date -d "+$1 days" "+$2" 2>/dev/null || date -v+"$1"d "+$2"; }

GATEWAY=${GATEWAY:-http://localhost:8080}
PASSWORD='eDemand-2026!'
FAILED=0

fault() { docker compose exec -T payment-service python -m app.faults "$@" >/dev/null; }
# Never leave a fault switched on, even if the script is stopped half-way (Ctrl-C).
trap 'fault reset' EXIT
trap 'exit 130' INT TERM
intents_for() { docker compose exec -T payment-db psql -U payments -tAc "select count(*) from payment_intents where order_id = $1" | tr -d '[:space:]'; }
order_status() {
  docker compose exec -T monolith node -e "const D=require('better-sqlite3');const o=new D('/app/data/edemand.db',{readonly:true}).prepare('select status from orders where id=?').get($1);console.log(o?o.status:'none')"
}
attempts_for() {  # the monolith's log lines for this order's calls to payment-service
  docker compose logs --no-log-prefix monolith 2>/dev/null | grep "\"orderId\":$1," | grep '"payment-service call' \
    | sed -E 's/.*"attempt":([0-9]+).*"(reason|status)":("[^"]*"|[0-9]+).*"ms":([0-9]+).*"msg":"payment-service call ([^"]*)".*/      attempt \1: \3 after \4 ms -> \5/'
}

C=$(curl -s -X POST "$GATEWAY/api/auth/customer/login" -H 'content-type: application/json' \
  -d "{\"email\":\"aom@example.com\",\"password\":\"$PASSWORD\"}" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
[ -n "$C" ] || { echo "could not log in — is the stack up?"; exit 1; }

# Place a gateway booking with FixIt (provider 2) on the first free slot. Prints "<http status> <order id> <seconds>".
book() {
  local i day t out status id
  for i in $(seq 2 90); do   # first free weekday slot in the next 90 days
    [ "$(days_ahead "$i" %u)" -lt 6 ] || continue
    day=$(days_ahead "$i" %F)
    for t in 09:00 10:30 12:00 13:30 15:00 16:30; do
      out=$(curl -s -w '\n%{http_code} %{time_total}' -X POST "$GATEWAY/api/bookings" -H "authorization: Bearer $C" \
        -H 'content-type: application/json' \
        -d "{\"provider_id\":2,\"service_date\":\"$day\",\"starting_time\":\"$t\",\"items\":[{\"service_id\":4,\"quantity\":1}],\"payment_method\":\"gateway\"}")
      status=$(echo "$out" | tail -1 | cut -d' ' -f1)
      [ "$status" = 409 ] && continue   # slot taken, try the next one
      id=$(echo "$out" | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p')
      [ -z "$id" ] && id=$(docker compose exec -T monolith node -e "const D=require('better-sqlite3');console.log(new D('/app/data/edemand.db',{readonly:true}).prepare(\"select max(id) m from orders where customer_id=3\").get().m)")
      echo "$status $id $(echo "$out" | tail -1 | cut -d' ' -f2)"
      return
    done
  done
}

expect() {  # <label> <expected> <actual>
  if [ "$2" = "$3" ]; then printf '    PASS  %-34s %s\n' "$1" "$3"; else printf '    FAIL  %-34s got %s, expected %s\n' "$1" "$3" "$2"; FAILED=1; fi
}

scenario() {  # <title> <fault args> <expected http status> <expected order status>
  echo
  echo "== $1"
  fault reset; fault $2
  read -r status id secs <<< "$(book)"
  fault reset
  echo "    POST /api/bookings -> HTTP $status in ${secs}s, order #$id"
  attempts_for "$id"
  expect "booking answer" "$3" "$status"
  expect "order status in the monolith" "$4" "$(order_status "$id")"
  expect "payment intents for #$id" "$5" "$(intents_for "$id")"
}

scenario "Fault 1: the reply is lost after the write (lost_reply 1)" "lost_reply 1" 201 awaiting 1
scenario "Fault 2: the reply is slower than the 2 s timeout (slow 1, 5 s)" "slow 1 5" 201 awaiting 1
scenario "Fault 3: payment-service fails for a 3 s window (outage 3)" "outage 3" 201 awaiting 1
scenario "Beyond the retries: a 30 s outage -> the saga compensates" "outage 30" 503 cancelled 0

echo
echo "payment-service's side (the Idempotency-Key at work):"
docker compose logs --no-log-prefix --since 3m payment-service 2>/dev/null \
  | grep -oE '"msg": "(idempotent replay|payment intent saved|fault: [^"]*)"(, "order_id": [0-9]+)?' | uniq -c | tail -12 | sed 's/^/  /'

echo
[ $FAILED -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $FAILED
