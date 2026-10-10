export const API = "/api/v1";

// `errorId` greps the server log line (`main.py::_error_response`).
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

export type FailureKind = "transient" | "auth" | "gone" | "denied" | "invalid";

export function failureKind(e: unknown): FailureKind {
  if (!(e instanceof ApiError)) return "transient";
  if (e.status === 401) return "auth";
  if (e.status === 403) return "denied";
  if (e.status === 404) return "gone";
  if (e.status === 400 || e.status === 422) return "invalid";
  return "transient";
}

// Tolerant: a proxy 502 or a static-export 404 answers HTML, not an envelope.
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

export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const init: RequestInit = { cache: "no-store" };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
  if (!r.ok) throw await toApiError(r, url);
  return (await r.json()) as T;
}

// A READ whose subject will not fit in a URL, so it mints no `Idempotency-Key`.
export async function jpost<T>(
  url: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  };
  if (signal) init.signal = signal;
  const r = await fetch(url, init);
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
