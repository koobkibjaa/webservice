#!/usr/bin/env bash
# Area D race test: 25 payment-gateway callbacks at the same instant, twice, and still exactly one
# charge per booking. Books two unpaid gateway bookings through the gateway, then runs
# payment-service/tests/race_webhook.py inside the payment-service container (it needs to reach
# the service and its database directly). Run from the repository root. Exit code 0 = pass.
set -uo pipefail
# A date N days from today: GNU date (Linux, Git Bash) or BSD date (macOS).
days_ahead() { date -d "+$1 days" "+$2" 2>/dev/null || date -v+"$1"d "+$2"; }

GATEWAY=${GATEWAY:-http://localhost:8080}
C=$(curl -s -X POST "$GATEWAY/api/auth/customer/login" -H 'content-type: application/json' \
  -d '{"email":"aom@example.com","password":"eDemand-2026!"}' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
[ -n "$C" ] || { echo "could not log in — is the stack up?"; exit 1; }

book() {  # a gateway booking with Sparkle (provider 1) on the first free slot -> order id
  local i day t id
  for i in $(seq 2 90); do   # first free weekday slot in the next 90 days
    [ "$(days_ahead "$i" %u)" -lt 6 ] || continue
    day=$(days_ahead "$i" %F)
    for t in 09:00 10:30 12:00 13:30 15:00 16:30; do
      id=$(curl -s -X POST "$GATEWAY/api/bookings" -H "authorization: Bearer $C" -H 'content-type: application/json' \
        -d "{\"provider_id\":1,\"service_date\":\"$day\",\"starting_time\":\"$t\",\"items\":[{\"service_id\":2,\"quantity\":1}],\"payment_method\":\"gateway\"}" \
        | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p')
      [ -n "$id" ] && { echo "$id"; return; }
    done
  done
}

A=$(book); B=$(book)
echo "== Two unpaid gateway bookings: #$A and #$B"
docker compose exec -T payment-service python -m tests.race_webhook "$A" "$B"
