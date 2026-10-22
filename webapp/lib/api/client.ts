export const API = "/api/v1";

// Stamped by the build (`next.config.ts`); the server names the build it serves on every API answer.
const BUILD = process.env.NEXT_PUBLIC_WEBAPP_BUILD;
let serverMovedOn = false;

export function buildIsStale(): boolean {
  return serverMovedOn;
}

// The ONE door to the API: every call is uncached, and every answer is checked against this tab's build.
export async function send(url: string, init: RequestInit = {}): Promise<Response> {
  const r = await fetch(url, { cache: "no-store", ...init });
  const serving = r.headers.get("X-Webapp-Build");
  // Only noted, never acted on here: a reload would drop an ingest draft or a command in flight.
  if (BUILD && serving && serving !== BUILD) serverMovedOn = true;
  return r;
}

// `errorId` greps the server log line (`main.py::_error_response`).
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly url: string,
    readonly code: string | null = null,
    readonly errorId: string | null = null,
    readonly details: Record<string, unknown> | null = null,
    readonly serverMessage: string | null = null,
  ) {
    super(serverMessage ?? `${status} ${url}`);
    this.name = "ApiError";
  }
}

export type FailureKind = "transient" | "auth" | "gone" | "denied" | "invalid" | "refused";

export function failureKind(e: unknown): FailureKind {
  if (!(e instanceof ApiError)) return "transient";
  if (e.status === 401) return "auth";
  if (e.status === 403) return "denied";
  if (e.status === 404) return "gone";
  if (e.status === 400 || e.status === 422) return "invalid";
  // The server answered and said no (a conflict, a spend cap): its sentence is the remedy.
  if (e.status >= 400 && e.status < 500) return "refused";
  return "transient";
}

// Tolerant: a proxy 502 or a static-export 404 answers HTML, not an envelope.
async function toApiError(r: Response, url: string): Promise<ApiError> {
  try {
    const body = (await r.json()) as {
      error?: unknown;
      message?: unknown;
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
      typeof body.message === "string" && body.message ? body.message : null,
    );
  } catch {
    return new ApiError(r.status, url);
  }
}

export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await send(url, { signal: signal ?? null });
  if (!r.ok) throw await toApiError(r, url);
  return (await r.json()) as T;
}

// A READ whose subject will not fit in a URL, so it mints no `Idempotency-Key`.
export async function jpost<T>(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const r = await send(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: signal ?? null,
  });
  if (!r.ok) throw await toApiError(r, url);
  return (await r.json()) as T;
}

export type Conditional<T> =
  | { kind: "ok"; data: T; validator: string | null }
  // No validator on purpose: a caller storing a proxy-stripped `null` would go unconditional forever.
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
  const r = await send(url, { headers, signal: signal ?? null });
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
