// Per-user identity writes to the auth router, not `/commands` verbs: no campaign, no cycle, no
// idempotency key.

import { API } from "./client";
import { throwApiError } from "./errors";
import type { UserSettings } from "./types";

export async function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  const r = await fetch(`${API}/auth/user-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as UserSettings;
}
// `version` is the live `me.terms_version`; a stale one 409s so the gate re-renders current text.
export async function acceptTerms(version: string): Promise<void> {
  const r = await fetch(`${API}/auth/accept-terms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
}
export async function postLogout(): Promise<void> {
  const r = await fetch(`${API}/auth/logout`, {
    method: "POST",
    cache: "no-store",
  });
  if (!r.ok) await throwApiError(r);
}
