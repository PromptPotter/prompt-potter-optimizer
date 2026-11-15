import { API, send, toApiError } from "./client";
import { commands } from "./commands";
import type { AccountKey, AccountTokens, MintedToken, UserSettings } from "@/lib/api/types";

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
// The token's text rides this answer alone; the listing names it by id, name and scopes.
export function mintApiToken(name: string, scopes: string[]): Promise<MintedToken> {
  return commands["mint-api-token"]({ name, scopes });
}
export function listApiTokens(): Promise<AccountTokens> {
  return commands["list-api-tokens"]({});
}
export async function revokeApiToken(tokenId: string): Promise<void> {
  await commands["revoke-api-token"]({ token_id: tokenId });
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
