// API base + the shared GET helper. Internal to the api layer — `reads`
// and `mutations` import it; it is not part of the `@/lib/api` surface.

export const API = "/api/v1";

// All reads are live (poll-driven). Pairs with the server-side
// `Cache-Control: no-store` header on `/api/v1/*` so neither layer can
// serve a stale response — the webapp is a real-time view of disk.
export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return (await r.json()) as T;
}
