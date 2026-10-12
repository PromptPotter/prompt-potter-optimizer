import { API, send } from "./client";
import { throwApiError } from "./errors";
import type { UserSettings } from "./types";

export async function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  const r = await send(`${API}/auth/user-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!r.ok) await throwApiError(r);
  return (await r.json()) as UserSettings;
}
// `version` is the live `me.terms_version`; a stale one 409s so the gate re-renders current text.
export async function acceptTerms(version: string): Promise<void> {
  const r = await send(`${API}/auth/accept-terms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!r.ok) await throwApiError(r);
}
export async function postLogout(): Promise<void> {
  const r = await send(`${API}/auth/logout`, {
    method: "POST",
  });
  if (!r.ok) await throwApiError(r);
}
