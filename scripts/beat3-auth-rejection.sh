#!/usr/bin/env bash
# Beat 3 — a bad token is refused with 401.
#
# Every call to notification-service must carry a Keycloak access token, and the service checks it
# itself (signature, expiry, issuer, audience), then checks that the caller owns the record.
# This script sends one good token and four bad ones through the gateway, then tries to touch
# another user's notification. Needs only bash and curl. Exit code 0 = every check as expected.
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
b64url_decode() { local s; s=$(printf '%s' "$1" | tr '_-' '/+'); case $(( ${#s} % 4 )) in 2) s="$s==";; 3) s="$s=";; esac; printf '%s' "$s" | base64 -d; }
b64url_encode() { base64 | tr -d '\n=' | tr '+/' '-_'; }

check() {  # <label> <expected status> <curl args...>
  local label=$1 expected=$2; shift 2
  local body status
  body=$(curl -s -w '\n%{http_code}' "$@")
  status=${body##*$'\n'}; body=${body%$'\n'*}
  if [ "$status" = "$expected" ]; then printf '  PASS  %-44s %s  %s\n' "$label" "$status" "${body:0:90}"
  else printf '  FAIL  %-44s got %s, expected %s  %s\n' "$label" "$status" "$expected" "${body:0:90}"; FAILED=1; fi
}

echo "== Getting tokens from Keycloak ($KEYCLOAK)"
NONG=$(kc_token homeservice-mobile customer-1)       # Nong, customer id 1
SOMCHAI=$(kc_token homeservice-mobile customer-2)    # Somchai, customer id 2
[ -n "$NONG" ] && [ -n "$SOMCHAI" ] || { echo "could not get tokens from Keycloak — is the stack up?"; exit 1; }

# Make sure Nong owns at least one notification (fresh boot): book, and let the provider confirm.
if [ "$(curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $NONG")" = "[]" ]; then
  echo "== Nong has no notifications yet: booking a slot so the provider's confirm creates one"
  login() { curl -s -X POST "$GATEWAY/api/auth/$1/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$2\",\"password\":\"$PASSWORD\"}" | sed -n 's/.*"token":"\([^"]*\)".*/\1/p'; }
  C=$(login customer nong@example.com); P=$(login provider sparkle@example.com)
  for i in 2 3 4 5 6 7 8; do [ "$(date -d "+$i days" +%u)" -lt 6 ] && { DAY=$(date -d "+$i days" +%F); break; }; done
  ORDER=$(curl -s -X POST "$GATEWAY/api/bookings" -H "authorization: Bearer $C" -H 'content-type: application/json' \
    -d "{\"provider_id\":1,\"address_id\":1,\"service_date\":\"$DAY\",\"starting_time\":\"09:00\",\"items\":[{\"service_id\":2,\"quantity\":1}],\"payment_method\":\"cod\"}" \
    | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p')
  curl -s -o /dev/null -X POST "$GATEWAY/api/bookings/$ORDER/confirm" -H "authorization: Bearer $P"
  sleep 2   # outbox -> RabbitMQ -> notification-service
fi
NOTE_ID=$(curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $NONG" | sed -n 's/^\[{"id":\([0-9]*\).*/\1/p')

# Forged: Nong's real token with the payload edited to claim Somchai's id. The signature no longer
# matches, which only a service that verifies signatures will notice.
IFS=. read -r H P S <<< "$NONG"
FORGED_PAYLOAD=$(b64url_decode "$P" | sed 's/"app_user_id":1,/"app_user_id":2,/' | b64url_encode)
FORGED="$H.$FORGED_PAYLOAD.$S"

echo "== Expired: a token from the expiry-demo client lives 3 seconds; waiting 5"
EXPIRED=$(kc_token expiry-demo customer-1)
sleep 5

echo "== Wrong audience: a valid Keycloak token, but issued for reporting-service"
WRONG_AUD=$(kc_token reporting-app customer-1)

echo
echo "== Calls through the gateway ($GATEWAY/api/notifications -> notification-service)"
check "valid token (Nong)"                       200 "$GATEWAY/api/notifications" -H "authorization: Bearer $NONG"
check "no token"                                 401 "$GATEWAY/api/notifications"
check "forged token (payload edited)"            401 "$GATEWAY/api/notifications" -H "authorization: Bearer $FORGED"
check "expired token"                            401 "$GATEWAY/api/notifications" -H "authorization: Bearer $EXPIRED"
check "wrong-audience token"                     401 "$GATEWAY/api/notifications" -H "authorization: Bearer $WRONG_AUD"
check "Somchai marks Nong's notification #$NOTE_ID read" 403 -X POST "$GATEWAY/api/notifications/$NOTE_ID/read" -H "authorization: Bearer $SOMCHAI"
check "Nong marks her own notification #$NOTE_ID read"   200 -X POST "$GATEWAY/api/notifications/$NOTE_ID/read" -H "authorization: Bearer $NONG"

echo
[ $FAILED -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $FAILED
