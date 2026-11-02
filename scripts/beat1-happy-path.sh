#!/usr/bin/env bash
# Beat 1 — the happy path, across every boundary.
#
# Nong books a sofa clean with Sparkle and pays by gateway; Sparkle confirms, starts and completes
# the job with Nong's OTP. Each step says which boundaries the request crossed and checks the
# result in the service that owns it:
#   browser -> gateway -> monolith                       (nginx routes by path)
#   monolith -> Keycloak                                 (password check, then a Keycloak token)
#   monolith -> payment-service  HTTP                    (the place-booking saga, step 2)
#   payment-service -> RabbitMQ -> monolith              (payment.succeeded: the order is paid)
#   monolith -> RabbitMQ -> payment-service              (booking.completed: the wallet is credited)
#   monolith -> RabbitMQ -> notification-service         (every booking event becomes a notification)
#   browser -> gateway -> payment-service / notification-service   (read with the Keycloak token)
# Needs only bash and curl. Exit code 0 = every check as expected. Safe to run again.
set -uo pipefail

GATEWAY=${GATEWAY:-http://localhost:8080}
PASSWORD='eDemand-2026!'
FAILED=0

field() { sed -n "s/.*\"$1\":\"\{0,1\}\([^\",}]*\).*/\1/p" | head -1; }   # first "<name>": value in a JSON body
step() { echo; echo "== $1"; echo "   crosses: $2"; }
expect() {  # <label> <expected> <actual>
  if [ "$2" = "$3" ]; then printf '   PASS  %-46s %s\n' "$1" "$3"; else printf '   FAIL  %-46s got %s, expected %s\n' "$1" "$3" "$2"; FAILED=1; fi
}
wait_for() {  # <expected> <command...>: poll up to 10 s for an eventually consistent result
  local want=$1 got; shift
  for _ in $(seq 1 20); do got=$("$@"); [ "$got" = "$want" ] && break; sleep 0.5; done
  echo "$got"
}
login() {  # <role> <email> -> the login response body
  curl -s -X POST "$GATEWAY/api/auth/$1/login" -H 'content-type: application/json' \
    -d "{\"email\":\"$2\",\"password\":\"$PASSWORD\"}"
}

step "1. Nong and Sparkle log in" "gateway -> monolith (password check) -> Keycloak (token for the services)"
NONG=$(login customer nong@example.com); SPARKLE=$(login provider sparkle@example.com)
C=$(echo "$NONG" | field token); CK=$(echo "$NONG" | field keycloak_token)
P=$(echo "$SPARKLE" | field token); PK=$(echo "$SPARKLE" | field keycloak_token)
expect "Nong has a monolith token"          yes "$([ -n "$C" ] && echo yes || echo no)"
expect "Nong has a Keycloak token"          yes "$([ -n "$CK" ] && echo yes || echo no)"
expect "Sparkle has a Keycloak token"       yes "$([ -n "$PK" ] && echo yes || echo no)"
[ -n "$C" ] && [ -n "$PK" ] || { echo "cannot continue — is the stack up?"; exit 1; }

WALLET_BEFORE=$(curl -s "$GATEWAY/api/provider/wallet" -H "authorization: Bearer $PK" | field balance_cents)

step "2. Nong books Sofa Shampoo with Sparkle, paying by gateway" \
     "gateway -> monolith (order, slot holds) -> payment-service over HTTP (payment intent) -> monolith outbox"
for i in 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  [ "$(date -d "+$i days" +%u)" -lt 6 ] || continue
  day=$(date -d "+$i days" +%F)
  for t in 09:00 10:30 12:00 13:30 15:00 16:30; do
    BOOKING=$(curl -s -w '\n%{http_code}' -X POST "$GATEWAY/api/bookings" -H "authorization: Bearer $C" -H 'content-type: application/json' \
      -d "{\"provider_id\":1,\"address_id\":1,\"service_date\":\"$day\",\"starting_time\":\"$t\",\"items\":[{\"service_id\":2,\"quantity\":1}],\"payment_method\":\"gateway\"}")
    [ "$(echo "$BOOKING" | tail -1)" = 201 ] && break 2
  done
done
ORDER=$(echo "$BOOKING" | sed -n 's/.*"order":{"id":\([0-9]*\).*/\1/p')
OTP=$(echo "$BOOKING" | field otp)
echo "   booking #$ORDER on $day at $t, total $(echo "$BOOKING" | field total_cents) cents, OTP $OTP"
expect "booking answer"                     201 "$(echo "$BOOKING" | tail -1)"
expect "payment intent from payment-service" requires_payment "$(echo "$BOOKING" | sed -n 's/.*"payment_intent":{[^}]*"status":"\([a-z_]*\)".*/\1/p')"

step "3. Nong pays" \
     "gateway -> payment-service (charge, outbox) -> RabbitMQ -> monolith (order paid) and notification-service"
PAY=$(curl -s -X POST "$GATEWAY/api/payments/simulate" -H "authorization: Bearer $CK" -H 'content-type: application/json' -d "{\"order_id\":$ORDER}")
expect "payment settled"                    true "$(echo "$PAY" | field settled)"
paid() { curl -s "$GATEWAY/api/bookings/$ORDER" -H "authorization: Bearer $C" | sed -n 's/.*"payment_status":"\([a-z]*\)".*/\1/p'; }
expect "monolith order marked paid (event)" paid "$(wait_for paid paid)"

step "4. Sparkle confirms, starts and completes the job with Nong's OTP" \
     "gateway -> monolith (status machine, outbox) -> RabbitMQ -> payment-service and notification-service"
for s in confirm start; do
  expect "$s" 200 "$(curl -s -o /dev/null -w '%{http_code}' -X POST "$GATEWAY/api/bookings/$ORDER/$s" -H "authorization: Bearer $P")"
done
DONE=$(curl -s -w '\n%{http_code}' -X POST "$GATEWAY/api/bookings/$ORDER/complete" \
  -H "authorization: Bearer $P" -H 'content-type: application/json' -d "{\"otp\":$OTP}")
expect "complete with the OTP" 200 "$(echo "$DONE" | tail -1)"
EARNINGS=$(echo "$DONE" | field provider_earnings_cents)   # total minus commission, fixed at completion
echo "   Sparkle earns $EARNINGS cents (commission $(echo "$DONE" | field commission_cents))"

step "5. Sparkle's wallet has the earnings" "gateway -> payment-service (credited from booking.completed)"
balance() { curl -s "$GATEWAY/api/provider/wallet" -H "authorization: Bearer $PK" | field balance_cents; }
WANT=$(( ${WALLET_BEFORE:-0} + EARNINGS ))
expect "balance went up by $EARNINGS"       "$WANT" "$(wait_for "$WANT" balance)"

step "6. Nong's notifications tell the story" "gateway -> notification-service (built from the booking events)"
# confirmed, started, completed, and the review request (completion emits two events at once, so
# their order in the list is not fixed; count them instead).
count() { curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $CK" | grep -o "\"body\":\"Booking #$ORDER[ ,]" | wc -l | tr -d ' '; }
expect "Nong's notifications for #$ORDER"   4 "$(wait_for 4 count)"
curl -s "$GATEWAY/api/notifications" -H "authorization: Bearer $CK" | grep -o "\"title\":\"[^\"]*\",\"body\":\"Booking #$ORDER[^\"]*\"" \
  | sed 's/"title":"\([^"]*\)","body":"\([^"]*\)"/     \1: \2/'
echo "   (Sparkle was told about the new booking and the payment: see her notifications.)"

step "7. Nong's transactions" "gateway -> payment-service"
expect "a charge for #$ORDER"               charge "$(curl -s "$GATEWAY/api/customer/transactions" -H "authorization: Bearer $CK" \
  | grep -o "{[^}]*\"order_id\":$ORDER,\"kind\":\"charge\"" | grep -o 'charge' | head -1)"

echo
[ $FAILED -eq 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit $FAILED
