export const runtime = "nodejs";
export const dynamic = "force-dynamic";

import { handle } from "#lib/http/handle.js";
import { json } from "#lib/http/respond.js";
import { getAuth, clearCookie, clearKcCookie } from "#lib/http/auth.js";

export const POST = handle(async (req) => {
  getAuth(req); // any authenticated user
  const res = json({ ok: true }, { status: 200, headers: { "Set-Cookie": clearCookie() } });
  res.headers.append("Set-Cookie", clearKcCookie());
  return res;
});
