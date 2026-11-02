// Server-Component helper: read from an extracted service as the signed-in browser user, with the
// Keycloak token from the kc_token cookie set at login. Returns the JSON body, or null if there is
// no token or the service does not answer in time — pages show "unavailable" instead of failing.
import { cookies } from "next/headers";

export async function fetchAsUser(url) {
  const token = cookies().get("kc_token")?.value;
  if (!token) return null;
  try {
    const res = await fetch(url, {
      headers: { authorization: `Bearer ${token}` },
      cache: "no-store",
      signal: AbortSignal.timeout(2000),
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}
