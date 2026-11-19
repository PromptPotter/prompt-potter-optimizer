import {
  FEED_SUBJECT_HEADER,
  FEED_VERSIONS_HEADER,
  SERVED_CONTRACT,
  type ErrorEnvelope,
  type FeedVersion,
  type NextAct,
} from "@/lib/api/types";

export const API = "/api/v1";

let serverMovedOn = false;

export function buildIsStale(): boolean {
  return serverMovedOn;
}

// Every answer and every stream frame names the contract it was served under; one other than this
// tab's is only noted, never acted on here: a reload would drop an ingest draft or a command in flight.
export function noteServedContract(serving: string | null): void {
  if (serving && serving !== SERVED_CONTRACT) serverMovedOn = true;
}

// The ONE door to the API: every call is uncached, and every answer is checked against the contract
// this tab was built against.
export async function send(url: string, init: RequestInit = {}): Promise<Response> {
  const r = await fetch(url, { cache: "no-store", ...init });
  noteServedContract(r.headers.get("X-Served-Contract"));
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
    readonly nextActs: readonly NextAct[] = [],
  ) {
    super(serverMessage ?? `${status} ${url}`);
    this.name = "ApiError";
  }
}

export type FailureKind =
  | "transient"
  | "throttled"
  | "auth"
  | "gone"
  | "denied"
  | "invalid"
  | "refused";

// A read refused client-side because the answer is not the kind it serves: no server said it.
export class InvalidInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InvalidInputError";
  }
}

export function failureKind(e: unknown): FailureKind {
  if (e instanceof InvalidInputError) return "invalid";
  if (!(e instanceof ApiError)) return "transient";
  if (e.status === 401) return "auth";
  if (e.status === 403) return "denied";
  if (e.status === 404) return "gone";
  if (e.status === 400 || e.status === 422) return "invalid";
  // The request rate's throttle (`quota.py::RateLimitedError`), the one 429: it passes, and the
  // act never ran.
  if (e.status === 429) return "throttled";
  // The server answered and said no (a conflict, a spend cap): its sentence is the remedy.
  if (e.status >= 400 && e.status < 500) return "refused";
  return "transient";
}

// The failures that pass, saying nothing of the address or the session: the server unreachable,
// its throttle. Any other kind the server would repeat; a 404 the cache alone re-asks, until its
// gone verdict (`read-cache.ts::GONE_CONFIRM_LIMIT`).
export function failurePasses(kind: FailureKind): boolean {
  return kind === "transient" || kind === "throttled";
}

// The ONE envelope parser, reads and writes alike. A proxy 502 or a missing-page 404 answers
// something else, which carries no `error_id`.
export async function toApiError(r: Response): Promise<ApiError> {
  const body: unknown = await r.json().catch(() => null);
  if (typeof body !== "object" || body === null || !("error_id" in body)) {
    return new ApiError(r.status, r.url);
  }
  const { code, error_id, details, message, next_acts } = body as ErrorEnvelope;
  // An empty sentence is no sentence: `operatorMessage` falls back to the kind's.
  return new ApiError(r.status, r.url, code, error_id, details, message || null, next_acts);
}

export async function jget<T>(url: string, signal?: AbortSignal): Promise<T> {
  const r = await send(url, { signal: signal ?? null });
  if (!r.ok) throw await toApiError(r);
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
  if (!r.ok) throw await toApiError(r);
  return (await r.json()) as T;
}

// Each feed subject a read's answer stood on, or a command's write produced, at its version
// (`FEED_VERSIONS_HEADER`).
export type FeedVersions = Readonly<Record<string, FeedVersion>>;

export function versionsSaid(r: Response): FeedVersions | undefined {
  const said = r.headers.get(FEED_VERSIONS_HEADER);
  return said === null ? undefined : (JSON.parse(said) as FeedVersions);
}

// `stood` is absent where the read named no subject, or its server said none.
export type Conditional<T> =
  | { kind: "ok"; data: T; validator: string | null; stood?: FeedVersions }
  // No validator on purpose: a caller storing a proxy-stripped `null` would go unconditional forever.
  | { kind: "not_modified"; stood?: FeedVersions };

// *subject* is the feed subject the read names, by prefix: its answer says the versions it stood on.
export async function jgetIfNoneMatch<T>(
  url: string,
  validator?: string | null,
  signal?: AbortSignal,
  subject?: string,
): Promise<Conditional<T>> {
  const headers: Record<string, string> = {};
  if (validator) headers["If-None-Match"] = validator;
  if (subject !== undefined) headers[FEED_SUBJECT_HEADER] = subject;
  const r = await send(url, { headers, signal: signal ?? null });
  const said = versionsSaid(r);
  const stood = said === undefined ? {} : { stood: said };
  if (r.status === 304) return { kind: "not_modified", ...stood };
  if (!r.ok) throw await toApiError(r);
  return { kind: "ok", data: (await r.json()) as T, validator: r.headers.get("ETag"), ...stood };
}
