#!/usr/bin/env bash
# Beat 4 — the same request, found as one trace in Jaeger.
#
# Places one gateway-paid booking through the gateway with a W3C traceparent this script chose,
# so we know its trace id in advance. That one request crosses both seams:
#   monolith --HTTP--> payment-service      (POST /internal/payment-intents, the saga's step 2)
#   monolith --outbox--> RabbitMQ --> notification-service   (booking.status_changed -> FixIt)
# The script then asks Jaeger for that trace id, lists the services and operations in it, and
# finds the same id in each service's logs (trace_id is the correlation id).
# Needs bash, curl and docker compose (run from the repository root). Exit code 0 = all found.
set -uo pipefail

GATEWAY=${GATEWAY:-http://localhost:8080}
JAEGER=${JAEGER:-http://localhost:16686}
hex() { od -An -N"$1" -tx1 /dev/urandom | tr -d ' \n'; }

C=$(curl -s -X POST "$GATEWAY/api/auth/customer/login" -H 'content-type: application/json' \
  -d '{"email":"aom@example.com","password":"eDemand-2026!"}' | sed -n 's/.*"token":"\([^"]*\)".*/\1/p')
[ -n "$C" ] || { echo "could not log in — is the stack up?"; exit 1; }

echo "== Placing a gateway-paid booking with FixIt, with our own traceparent"
TRACE=""
for i in 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  [ "$(date -d "+$i days" +%u)" -lt 6 ] || continue
  day=$(date -d "+$i days" +%F)
  for t in 09:00 10:30 12:00 13:30 15:00 16:30; do
    try=$(hex 16)   # a fresh trace id per attempt, so a taken slot (409) is not mixed in
    out=$(curl -s -w '\n%{http_code}' -X POST "$GATEWAY/api/bookings" \
      -H "traceparent: 00-$try-$(hex 8)-01" -H "authorization: Bearer $C" -H 'content-type: application/json' \
      -d "{\"provider_id\":2,\"service_date\":\"$day\",\"starting_time\":\"$t\",\"items\":[{\"service_id\":4,\"quantity\":1}],\"payment_method\":\"gateway\"}")
    if [ "$(echo "$out" | tail -1)" = 201 ]; then
      TRACE=$try; ORDER=$(echo "$out" | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p'); break 2
    fi
  done
done
[ -n "$TRACE" ] || { echo "no free slot found"; exit 1; }
echo "   booking #$ORDER created; trace id $TRACE"

echo "== Waiting for Jaeger to have every service's spans"
for _ in $(seq 1 20); do
  JSON=$(curl -s "$JAEGER/api/traces/$TRACE")
  SERVICES=$(echo "$JSON" | grep -o '"serviceName":"[^"]*"' | cut -d'"' -f4 | sort -u | tr '\n' ' ')
  case "$SERVICES" in *monolith*notification-service*payment-service*) break;; esac
  sleep 1
done

echo
echo "== Jaeger: trace $TRACE"
echo "   services in this one trace: $SERVICES"
echo "   operations (count, name):"
echo "$JSON" | grep -o '"operationName":"[^"]*"' | cut -d'"' -f4 \
  | grep -vE '^(resolve page components|start response)$' | sort | uniq -c | sort -rn | sed 's/^/     /'
echo "   open it: $JAEGER/trace/$TRACE"

echo
echo "== The same id in each service's logs (trace_id = correlation id)"
FAILED=0
for svc in monolith payment-service notification-service; do
  line=$(docker compose logs --no-log-prefix "$svc" 2>/dev/null | grep "$TRACE" | grep -oE '"msg": ?"[^"]*"' | tail -1)
  printf '   %-22s %s\n' "$svc" "${line:-(none)}"
done

echo
for svc in monolith payment-service notification-service; do
  case "$SERVICES" in *"$svc"*) printf '  PASS  %-22s is in the trace\n' "$svc";;
    *) printf '  FAIL  %-22s is missing from the trace\n' "$svc"; FAILED=1;; esac
done
for op in "POST /internal/payment-intents" "publish edemand.events"; do
  if echo "$JSON" | grep -q "\"operationName\":\"$op"; then printf '  PASS  span "%s"\n' "$op"
  else printf '  FAIL  span "%s" not found\n' "$op"; FAILED=1; fi
done
echo
[ $FAILED -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $FAILED
