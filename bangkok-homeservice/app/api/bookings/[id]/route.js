export const runtime = "nodejs";
export const dynamic = "force-dynamic";

import { handle } from "#lib/http/handle.js";
import { json } from "#lib/http/respond.js";
import { getAuth } from "#lib/http/auth.js";
import { parseId } from "#lib/http/validate.js";
import * as booking from "#lib/modules/booking/service.js";
import { findIntent } from "#lib/modules/booking/paymentClient.js";

export const GET = handle(async (req, { params }) => {
  const auth = getAuth(req, ["customer", "provider"]);
  const out = booking.getBooking(parseId(params.id, "booking id"), auth);
  // The intent lives in payment-service now. If it cannot be reached the booking is still shown,
  // just without payment_intent.
  if (out.payment_method === "gateway") {
    const intent = await findIntent(out.id);
    if (intent) out.payment_intent = intent;
  }
  return json(out);
});
