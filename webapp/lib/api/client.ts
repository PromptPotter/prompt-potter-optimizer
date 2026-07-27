// API base + the shared GET helper. Internal to the api layer — `reads`
// and `mutations` import it; it is not part of the `@/lib/api` surface.

export const API = "/api/v1";

// A non-2xx read. Carries the status as a field so callers branch on it
// (`err.status === 401` → needs-auth state; see lib/auth-context.tsx) instead
// of parsing the message string. The message stays technical for logs — a
// user-facing surface must render its own copy, never `err.message` raw
// (frontend-surface-contract.md § I2).
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
  ) {
    super(`${status} ${url}`);
    this.name = "ApiError";
  }
}

// All reads are live (poll-driven). Pairs with the server-side
// `Cache-Control: no-store` header on `/api/v1/*` so neither layer can
// serve a stale response — the webapp is a real-time view of disk.
export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  if (!r.ok) throw new ApiError(r.status, url);
  return (await r.json()) as T;
}

// Conditional GET — for poll loops over slow-changing files. The caller stores the
// validator the server issued and passes it back next tick; the server answers
// `304 Not Modified` (no body) when nothing changed. Returns a discriminated union
// so the caller cleanly skips work on the 304 path.
//
// TWO validator modes, because the two polls validate different things:
//   "mtime" — `Last-Modified` / `If-Modified-Since`. The dashboard poll, whose body
//             IS one file: an mtime says everything there is to say about it.
//   "etag"  — `ETag` / `If-None-Match`. The lineage-tree poll, whose body depends on
//             an mtime AND the request's lens/samples mask. A time validator cannot
//             express query-dependence, which is why masked tree reads used to be
//             carved out of the fast path and rebuilt on every single poll.
// One helper, one 304 path; the mode picks the header pair. The stored value is
// called a `validator` rather than a `lastModified` because under "etag" it is not
// a date, and a field that lies about what it holds is how the two got conflated.
export type ConditionalMode = "mtime" | "etag";

export type Conditional<T> =
  | { kind: "ok"; data: T; validator: string | null }
  | { kind: "not_modified"; validator: string | null };

export async function jgetConditional<T>(
  url: string,
  validator?: string | null,
  signal?: AbortSignal,
  mode: ConditionalMode = "mtime",
): Promise<Conditional<T>> {
  const [requestHeader, responseHeader] =
    mode === "etag"
      ? (["If-None-Match", "ETag"] as const)
      : (["If-Modified-Since", "Last-Modified"] as const);
  const headers: Record<string, string> = {};
  if (validator) headers[requestHeader] = validator;
  const init: RequestInit = { cache: "no-store", headers };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  const next = r.headers.get(responseHeader);
  if (r.status === 304) return { kind: "not_modified", validator: next };
  if (!r.ok) throw new ApiError(r.status, url);
  return { kind: "ok", data: (await r.json()) as T, validator: next };
}
