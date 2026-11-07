import { API, send, toApiError } from "./client";
import { commands } from "./commands";
import type { AccountKey, UserSettings } from "@/lib/api/types";

export function patchUserSettings(settings: UserSettings): Promise<UserSettings> {
  return commands["set-user-settings"](settings);
}
// The secret goes out once; every read after names its last four characters alone.
export function putApiKey(provider: string, secret: string): Promise<AccountKey> {
  return commands["set-api-key"]({ provider, secret });
}
export async function deleteApiKey(provider: string): Promise<void> {
  await commands["remove-api-key"]({ provider });
}
// `version` is the live `me.terms_version`; a stale one 409s so the gate re-renders current text.
export async function acceptTerms(version: string): Promise<void> {
  await commands["accept-terms"]({ version });
}
export async function postLogout(): Promise<void> {
  const r = await send(`${API}/auth/logout`, {
    method: "POST",
  });
  if (!r.ok) throw await toApiError(r);
}
