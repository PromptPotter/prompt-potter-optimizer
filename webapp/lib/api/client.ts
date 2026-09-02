// API base + the shared GET helper. Internal to the api layer — `reads`
// and `mutations` import it; it is not part of the `@/lib/api` surface.

export const API = "/api/v1";

// A non-2xx read. Carries the server's own classification so callers branch on
// data instead of parsing the message string. The message stays technical for
// logs — a user-facing surface must render its own copy, never `err.message`
// raw (frontend-surface-contract.md § I2).
//
// `code` and `errorId` come from the `ErrorEnvelope` the API serializes at ONE
// seam (`main.py::_error_response`, schema in api-openapi.yaml). The server
// already classifies every failure precisely; this type is what stops that
// classification being thrown away on arrival. `errorId` is the handle that
// reaches the server log line — quote it in a bug report.
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    readonly code: string | null = null,
    readonly errorId: string | null = null,
    readonly details: Record<string, unknown> | null = null,
  ) {
    super(`${status} ${url}`);
    this.name = "ApiError";
  }
}

// How a caller must REACT to a failure — the five reactions that differ, not a
// restatement of HTTP. Deliberately coarse: a poll needs to know "retry",
// "re-probe auth" or "stop, this address is dead", and nothing finer changes
// what it does.
//
// `transient` is the SAFE DEFAULT, and that direction matters: an unrecognised
// failure keeps retrying rather than destroying client state. Only an explicit
// 404 — the server answering, definitively, "no such thing" — is `gone`.
export type FailureKind = "transient" | "auth" | "gone" | "denied" | "invalid";

export function failureKind(e: unknown): FailureKind {
  if (!(e instanceof ApiError)) return "transient"; // network, parse, abort
  if (e.status === 401) return "auth";
  if (e.status === 403) return "denied";
  if (e.status === 404) return "gone";
  if (e.status === 400 || e.status === 422) return "invalid";
  return "transient"; // 5xx and anything unmapped
}

// Parse the `ErrorEnvelope` off a failed response. Tolerant by construction: a
// proxy 502 or a static-export 404 answers HTML, and a transport helper that
// throws while building an error is how one failure becomes two.
async function toApiError(r: Response, url: string): Promise<ApiError> {
  try {
    const body = (await r.json()) as {
      error?: unknown;
      error_id?: unknown;
      details?: unknown;
    };
    return new ApiError(
      r.status,
      url,
      typeof body.error === "string" ? body.error : null,
      typeof body.error_id === "string" ? body.error_id : null,
      body.details && typeof body.details === "object"
        ? (body.details as Record<string, unknown>)
        : null,
    );
  } catch {
    return new ApiError(r.status, url);
  }
}

// All reads are live (poll-driven). Pairs with the server-side
// `Cache-Control: no-store` header on `/api/v1/*` so neither layer can
// serve a stale response — the webapp is a real-time view of disk.
export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  if (!r.ok) throw await toApiError(r, url);
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
  // No validator on this arm, deliberately. A 304 asserts that the one the caller SENT is
  // still current, so there is nothing new to report and the caller already holds the answer.
  // Carrying a field here meant a response whose header a proxy stripped handed back `null`,
  // and a caller that stored it went out unconditional forever after — a permanent full-body
  // refetch every tick, with no error anywhere. Absent, that is unrepresentable.
  | { kind: "not_modified" };

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
  if (r.status === 304) return { kind: "not_modified" };
  if (!r.ok) throw await toApiError(r, url);
  return { kind: "ok", data: (await r.json()) as T, validator: r.headers.get(responseHeader) };
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
