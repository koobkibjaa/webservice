// Reading from an extracted service with a person's Keycloak token.
//   fetchJson(url, token)  the plain call (also what the Pact consumer test exercises)
//   fetchAsUser(url)       for Server Components: the signed-in browser user's token, from the
//                          kc_token cookie set at login
// Both return the JSON body, or null if there is no token or the service does not answer
// successfully in time — pages show "unavailable" instead of failing.
export async function fetchJson(url, token) {
  if (!token) return null;
  try {
    const res = await fetch(url, {
      headers: { authorization: `Bearer ${token}`, accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(2000),
    });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

export async function fetchAsUser(url) {
  const { cookies } = await import("next/headers");
  return fetchJson(url, cookies().get("kc_token")?.value);
}
