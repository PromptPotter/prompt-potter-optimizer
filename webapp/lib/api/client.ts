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
// TWO wrappers, because the two polls validate different things:
//   `jgetIfModified`  — `Last-Modified` / `If-Modified-Since`. The dashboard poll, whose
//                       body IS one file: an mtime says everything there is to say.
//   `jgetIfNoneMatch` — `ETag` / `If-None-Match`. The tree/ray polls, whose bodies depend
//                       on mtimes AND the request's query (lens/samples mask, ray window).
//                       A time validator cannot express query-dependence, which is why
//                       masked tree reads used to be rebuilt on every single poll.
// One body, one 304 path; the wrapper picks the header pair. The stored value is called
// a `validator` rather than a `lastModified` because under ETag it is not a date, and a
// field that lies about what it holds is how the two got conflated.
export type Conditional<T> =
  | { kind: "ok"; data: T; validator: string | null }
  | { kind: "not_modified"; validator: string | null };

async function jgetWithValidator<T>(
  url: string,
  requestHeader: "If-Modified-Since" | "If-None-Match",
  responseHeader: "Last-Modified" | "ETag",
  validator?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<T>> {
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

export function jgetIfModified<T>(
  url: string,
  validator?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<T>> {
  return jgetWithValidator<T>(url, "If-Modified-Since", "Last-Modified", validator, signal);
}

export function jgetIfNoneMatch<T>(
  url: string,
  validator?: string | null,
  signal?: AbortSignal,
): Promise<Conditional<T>> {
  return jgetWithValidator<T>(url, "If-None-Match", "ETag", validator, signal);
}
