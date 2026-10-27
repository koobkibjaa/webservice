# Domain event -> the notifications it produces. Ported one-for-one from the monolith's old relay
# (lib/modules/notifications/relay.js) so users see exactly the same messages after the split.
# Pure function: no I/O, so it is easy to test and to explain.


def _n(role, recipient_id, type_, title, body, order_id=None):
    return {"role": role, "id": recipient_id, "type": type_, "title": title, "body": body, "order_id": order_id}


def to_notifications(event_type, p):
    if event_type == "review.created":
        return [_n("provider", p["provider_id"], "new_review", "New review",
                   f"You received a {p['rating']}-star review on booking #{p['order_id']}.", p["order_id"])]

    if event_type == "booking.status_changed":
        # Notify the counterparty of whoever drove the transition.
        to_customer = p.get("actor_role") == "provider"
        role = "customer" if to_customer else "provider"
        recipient = p["customer_id"] if to_customer else p["provider_id"]
        return [_n(role, recipient, "booking_status", f"Booking {p['to']}",
                   f"Booking #{p['order_id']} is now {p['to']}.", p["order_id"])]

    if event_type == "booking.completed":
        return [_n("customer", p["customer_id"], "rating_request", "How did it go?",
                   f"Booking #{p['order_id']} is complete — leave a review for your provider.", p["order_id"])]

    if event_type == "bid.accepted":
        out = [_n("provider", p["winner_provider_id"], "bid_accepted", "Your bid was accepted",
                  f"Your bid on job #{p['job_request_id']} was accepted — booking #{p['order_id']} created.",
                  p["order_id"])]
        for loser in p.get("loser_provider_ids") or []:
            out.append(_n("provider", loser, "bid_rejected", "Bid not selected",
                          f"Your bid on job #{p['job_request_id']} was not selected."))
        return out

    if event_type == "payment.succeeded":
        if p.get("provider_id") is None:
            return []
        return [_n("provider", p["provider_id"], "payment_received", "Payment received",
                   f"Payment of {p['amount_cents']} cents was received for booking #{p['order_id']}.",
                   p["order_id"])]

    if event_type == "provider.approved":
        # Used to be a direct INSERT inside the monolith's approveProvider transaction.
        return [_n("provider", p["provider_id"], "provider_approved", "You're approved",
                   "Your provider account has been approved. Your services are now visible to customers.")]

    return []


ROUTING_KEYS = [
    "review.created",
    "booking.status_changed",
    "booking.completed",
    "bid.accepted",
    "payment.succeeded",
    "provider.approved",
]
