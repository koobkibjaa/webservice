#!/usr/bin/env bash
# Beat 3 — a bad token is refused with 401.
#
# Every call to an extracted service must carry a Keycloak access token, and each service checks
# it itself (signature, expiry, issuer, audience), then checks that the caller owns the record.
# For notification-service and payment-service in turn, this script sends one good token and four
# bad ones through the gateway, then tries to touch another customer's record.
# Needs only bash and curl. Exit code 0 = every check as expected. Safe to run again.
set -uo pipefail

GATEWAY=${GATEWAY:-http://localhost:8080}
KEYCLOAK=${KEYCLOAK:-http://localhost:8180}
TOKEN_URL="$KEYCLOAK/realms/homeservice/protocol/openid-connect/token"
PASSWORD='eDemand-2026!'
FAILED=0

kc_token() {  # <client_id> <keycloak username>
  curl -s -X POST "$TOKEN_URL" -d grant_type=password -d "client_id=$1" -d "username=$2" \
    --data-urlencode "password=$PASSWORD" | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}
login() {  # <role> <email> -> the monolith's own token
  curl -s -X POST "$GATEWAY/api/auth/$1/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$2\",\"password\":\"$PASSWORD\"}" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p'
}
book() {  # <monolith token> <cod|gateway> -> order id of a new booking with Sparkle, first free slot
  local day t id i
  for i in 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16; do
    [ "$(date -d "+$i days" +%u)" -lt 6 ] || continue
    day=$(date -d "+$i days" +%F)
    for t in 09:00 10:30 12:00 13:30 15:00 16:30; do
      id=$(curl -s -X POST "$GATEWAY/api/bookings" -H "authorization: Bearer $1" -H 'content-type: application/json' \
        -d "{\"provider_id\":1,\"address_id\":1,\"service_date\":\"$day\",\"starting_time\":\"$t\",\"items\":[{\"service_id\":2,\"quantity\":1}],\"payment_method\":\"$2\"}" \
        | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p')
      [ -n "$id" ] && { echo "$id"; return; }
    done
  done
}
b64url_decode() { local s; s=$(printf '%s' "$1" | tr '_-' '/+'); case $(( ${#s} % 4 )) in 2) s="$s==";; 3) s="$s=";; esac; printf '%s' "$s" | base64 -d; }
b64url_encode() { base64 | tr -d '\n=' | tr '+/' '-_'; }

check() {  # <label> <expected status> <curl args...>
  local label=$1 expected=$2; shift 2
  local body status
  body=$(curl -s -w '\n%{http_code}' "$@")
  status=${body##*$'\n'}; body=${body%$'\n'*}
  if [ "$status" = "$expected" ]; then printf '  PASS  %-46s %s  %s\n' "$label" "$status" "${body:0:80}"
  else printf '  FAIL  %-46s got %s, expected %s  %s\n' "$label" "$status" "$expected" "${body:0:80}"; FAILED=1; fi
}

echo "== Getting tokens from Keycloak ($KEYCLOAK)"
NONG=$(kc_token homeservice-mobile customer-1)       # Nong, customer id 1
SOMCHAI=$(kc_token homeservice-mobile customer-2)    # Somchai, customer id 2
[ -n "$NONG" ] && [ -n "$SOMCHAI" ] || { echo "could not get tokens from Keycloak — is the stack up?"; exit 1; }
C=$(login customer nong@example.com); P=$(login provider sparkle@example.com)

# Records for the ownership checks: a notification Nong owns (on a fresh boot: book, and let the
# provider confirm), and an unpaid gateway booking of hers with a payment intent.
if [ "$(curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $NONG")" = "[]" ]; then
  echo "== Nong has no notifications yet: booking a slot so the provider's confirm creates one"
  curl -s -o /dev/null -X POST "$GATEWAY/api/bookings/$(book "$C" cod)/confirm" -H "authorization: Bearer $P"
  sleep 2   # outbox -> RabbitMQ -> notification-service
fi
NOTE_ID=$(curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $NONG" | sed -n 's/^\[{"id":\([0-9]*\).*/\1/p')
echo "== Booking a gateway-paid slot for Nong (it gets a payment intent at payment-service)"
ORDER=$(book "$C" gateway)

# Forged: Nong's real token with the payload edited to claim Somchai's id. The signature no longer
# matches, which only a service that verifies signatures will notice.
IFS=. read -r H PL S <<< "$NONG"
FORGED="$H.$(b64url_decode "$PL" | sed 's/"app_user_id":1,/"app_user_id":2,/' | b64url_encode).$S"

echo "== Expired: a token from the expiry-demo client lives 3 seconds; waiting 5"
EXPIRED=$(kc_token expiry-demo customer-1)
sleep 5

echo "== Wrong audience: a valid Keycloak token, but issued for reporting-service"
WRONG_AUD=$(kc_token reporting-app customer-1)

for SVC in notification payment; do
  if [ $SVC = notification ]; then URL="$GATEWAY/api/notifications"; else URL="$GATEWAY/api/customer/transactions"; fi
  echo
  echo "== $SVC-service, through the gateway (${URL#$GATEWAY})"
  check "valid token (Nong)"              200 "$URL" -H "authorization: Bearer $NONG"
  check "no token"                        401 "$URL"
  check "forged token (payload edited)"   401 "$URL" -H "authorization: Bearer $FORGED"
  check "expired token"                   401 "$URL" -H "authorization: Bearer $EXPIRED"
  check "wrong-audience token"            401 "$URL" -H "authorization: Bearer $WRONG_AUD"
  if [ $SVC = notification ]; then
    check "Somchai marks Nong's notification #$NOTE_ID read" 403 -X POST "$URL/$NOTE_ID/read" -H "authorization: Bearer $SOMCHAI"
    check "Nong marks her own notification #$NOTE_ID read"   200 -X POST "$URL/$NOTE_ID/read" -H "authorization: Bearer $NONG"
  else
    check "Somchai pays Nong's booking #$ORDER" 403 -X POST "$GATEWAY/api/payments/simulate" \
      -H "authorization: Bearer $SOMCHAI" -H 'content-type: application/json' -d "{\"order_id\":$ORDER}"
    check "Nong pays her own booking #$ORDER"   200 -X POST "$GATEWAY/api/payments/simulate" \
      -H "authorization: Bearer $NONG" -H 'content-type: application/json' -d "{\"order_id\":$ORDER}"
  fi
done

echo
[ $FAILED -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $FAILED
