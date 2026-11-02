export const dynamic = "force-dynamic";

import { redirect } from "next/navigation";
import { getSession } from "#lib/http/session.js";
import { fetchAsUser } from "#lib/http/remote.js";
import { config } from "#lib/config.js";
import { ActionButton } from "../_ui/client.js";

// Notifications are owned by notification-service now, so this page reads them over HTTP with the
// user's own Keycloak token (the kc_token cookie set at login) instead of calling a service module
// in-process. If the service is down, or there is no Keycloak token, the rest of the monolith keeps
// working and only this page degrades. What this page needs from the reply is pinned down by the
// Pact contract in contracts/consumer/notification-service.pact.js.
export default async function NotificationsPage() {
  const session = getSession();
  if (!session) redirect("/login");
  const list = await fetchAsUser(`${config.notificationServiceUrl}/api/notifications`);

  return (
    <div>
      <h1>Notifications</h1>
      {list === null ? <p className="muted">Notifications are unavailable right now. Try again shortly, or sign out and in again.</p>
        : list.length === 0 ? <p className="muted">Nothing here yet.</p> : (
        <ul className="plain">
          {list.map((n) => (
            <li key={n.id} style={{ opacity: n.read_at ? 0.55 : 1 }}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <strong>{n.title}</strong> <span className="muted small">· {n.type}</span>
                  <div className="small">{n.body}</div>
                </div>
                {!n.read_at ? <ActionButton url={`/api/notifications/${n.id}/read`} label="Mark read" className="secondary" /> : <span className="muted small">read</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
