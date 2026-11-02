// Keycloak tokens for the extracted services. The monolith still checks passwords itself (it owns
// the accounts); once it has, it asks Keycloak for an access token for the same person, which the
// browser and the mobile app then present to notification-service.
//
// Keycloak usernames are `${role}-${id}` (e.g. customer-1): an email is only unique within one
// role table, so it cannot be the key. The seeded users are in keycloak/realm-homeservice.json;
// anyone who registered later is created in Keycloak on their first login (just-in-time).
//
// Never fatal: if Keycloak is down the login still succeeds with the monolith's own token, and
// only the pages that need an extracted service degrade.
import { config } from "../../config.js";
import { logger } from "../../logger.js";

const realmUrl = () => `${config.keycloakUrl}/realms/${config.keycloakRealm}`;
const TIMEOUT_MS = 3000;

async function tokenRequest(params) {
  const res = await fetch(`${realmUrl()}/protocol/openid-connect/token`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: config.keycloakClientId,
      client_secret: config.keycloakClientSecret,
      ...params,
    }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  return { status: res.status, body: await res.json() };
}

function passwordGrant(username, password) {
  return tokenRequest({ grant_type: "password", username, password, scope: "openid" });
}

// Create the person in Keycloak with the password the monolith has just verified, using the
// monolith client's own service account (it holds realm-management/manage-users).
async function provision(subject, username, password) {
  const admin = await tokenRequest({ grant_type: "client_credentials" });
  if (admin.status !== 200) throw new Error(`service-account token: ${admin.status}`);
  const res = await fetch(`${config.keycloakUrl}/admin/realms/${config.keycloakRealm}/users`, {
    method: "POST",
    headers: { "content-type": "application/json", authorization: `Bearer ${admin.body.access_token}` },
    body: JSON.stringify({
      username,
      email: subject.email,
      emailVerified: true,
      firstName: subject.name,
      lastName: subject.role,
      enabled: true,
      attributes: { app_user_id: [String(subject.id)], app_role: [subject.role] },
      credentials: [{ type: "password", value: password, temporary: false }],
    }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (res.status !== 201) throw new Error(`create user: ${res.status}`);
  logger.info({ username }, "keycloak: user provisioned");
}

// Returns { access_token, expires_in } or null.
export async function keycloakTokenFor(subject, password) {
  if (config.isTest) return null; // the in-process suite runs without Keycloak
  const username = `${subject.role}-${subject.id}`;
  try {
    let res = await passwordGrant(username, password);
    if (res.status === 401 && res.body.error === "invalid_grant") {
      await provision(subject, username, password);
      res = await passwordGrant(username, password);
    }
    if (res.status !== 200) throw new Error(`token: ${res.status} ${res.body.error ?? ""}`);
    return { access_token: res.body.access_token, expires_in: res.body.expires_in };
  } catch (err) {
    logger.warn({ username, err: err.message }, "keycloak: no token for this login");
    return null;
  }
}
