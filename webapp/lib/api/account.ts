import { API, send, toApiError } from "./client";
import type { AccountKey, UserSettings } from "./types";

export async function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  const r = await send(`${API}/auth/user-settings`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
  if (!r.ok) throw await toApiError(r);
  return (await r.json()) as UserSettings;
}
// The secret goes out once; every read after names its last four characters alone.
export async function putApiKey(provider: string, secret: string): Promise<AccountKey> {
  const r = await send(`${API}/auth/api-keys/${encodeURIComponent(provider)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ secret }),
  });
  if (!r.ok) throw await toApiError(r);
  return (await r.json()) as AccountKey;
}
export async function deleteApiKey(provider: string): Promise<void> {
  const r = await send(`${API}/auth/api-keys/${encodeURIComponent(provider)}`, {
    method: "DELETE",
  });
  if (!r.ok) throw await toApiError(r);
}
// `version` is the live `me.terms_version`; a stale one 409s so the gate re-renders current text.
export async function acceptTerms(version: string): Promise<void> {
  const r = await send(`${API}/auth/accept-terms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version }),
  });
  if (!r.ok) throw await toApiError(r);
}
export async function postLogout(): Promise<void> {
  const r = await send(`${API}/auth/logout`, {
    method: "POST",
  });
  if (!r.ok) throw await toApiError(r);
}
