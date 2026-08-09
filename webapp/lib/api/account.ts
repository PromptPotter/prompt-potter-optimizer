// Per-USER identity mutations — preferences, consent, sign-out. Not `/commands` verbs: these
// PATCH/POST the auth router directly, because they change who the caller is rather than what a
// campaign is doing, and so carry no idempotency key and no cycle to write a `CommandRecord` to.

import { API } from "./client";
import type { UserSettings } from "./types";

// Account → Preferences write. A user-account mutation (not a campaign
// command), so it PATCHes the auth router directly rather than `/commands`.
export async function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  const r = await fetch(`${API}/auth/user-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`user-settings PATCH failed (${r.status})`);
  return (await r.json()) as UserSettings;
}
// Record consent to the current Terms — the provable artifact behind the
// post-auth consent gate. Like user-settings, a per-user identity mutation on
// the auth router, not a `/commands` verb. `version` is the live
// `me.terms_version`; the server rejects a stale one (409) so the gate
// re-renders against current text. The accepted timestamp is server-stamped.
export async function acceptTerms(version: string): Promise<void> {
  const r = await fetch(`${API}/auth/accept-terms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`accept-terms failed (${r.status})`);
}
// Security pane sign-out. Not a command-highway POST (logout is
// auth-router-owned); writes the session-cookie clear via the server-side
// session store. On 200 the caller hard-redirects to /login.
export async function postLogout(): Promise<void> {
  const r = await fetch(`${API}/auth/logout`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) throw new Error(`${r.status} POST /auth/logout`);
}
