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

// Conditional GET — for poll loops over slow-changing files. The caller
// stores the previous `Last-Modified` response header and passes it back as
// `ifModifiedSince`; the server returns `304 Not Modified` (no body) when
// the on-disk mtime hasn't advanced. Returns a discriminated union so the
// caller cleanly skips work on the 304 path.
export type Conditional<T> =
  | { kind: "ok"; data: T; lastModified: string | null }
  | { kind: "not_modified"; lastModified: string | null };

export async function jgetConditional<T>(
  url: string,
  ifModifiedSince?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<T>> {
  const headers: Record<string, string> = {};
  if (ifModifiedSince) headers["If-Modified-Since"] = ifModifiedSince;
  const init: RequestInit = { cache: "no-store", headers };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  const lastModified = r.headers.get("Last-Modified");
  if (r.status === 304) return { kind: "not_modified", lastModified };
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return { kind: "ok", data: (await r.json()) as T, lastModified };
}
