// The write surface's failure vocabulary, and the idempotency key every command carries.
//
// Lands first in the dependency order: `commands.ts` and `ingest.ts` both throw through
// `throwApiError`, so it cannot live in either. `IngestApiError` is named for where it was
// first thrown, not for its scope — every write path raises it.
//
// It IS an `ApiError`, because one envelope gets one classifier. Declared beside that family
// rather than inside it, every write failure fell through `failureKind`'s `instanceof` to
// `transient` whatever the server had answered — so a refusal rendered as a dead network — and
// reached the incident ring with no `error_id`, code or status to grep the log by. What this
// class adds is the ingest DETAIL the read path has no use for.

import { ApiError, type FailureKind } from "./client";
import type { OriginGap } from "./draft-types";

export function mintIdempotencyKey(): string {
  // crypto.randomUUID is in every browser Next.js 16 supports + Node 18+.
  return crypto.randomUUID();
}
// A type alias rather than an interface, so it satisfies `ApiError.details`'
// `Record<string, unknown>`: an interface carries no implicit index signature.
export type IngestErrorDetail = {
  reason?: string;
  // On a `slug_collision` (409): the colliding dataset name + a free suggestion.
  // The chat offers "use existing {slug}" / "save as new {suggested_slug}".
  slug?: string;
  suggested_slug?: string;
  draft_id?: string;
  gaps?: OriginGap[];
};
export class IngestApiError extends ApiError {
  // The envelope's own operator sentence; null when the server answered without one.
  readonly serverMessage: string | null;
  readonly reason?: string;
  // `slug_collision` (409): the existing dataset name + a free suggestion.
  readonly existingSlug?: string;
  readonly suggestedSlug?: string;
  readonly draftId?: string;
  // Populated on `origin_incomplete` (422) — the deterministic checklist's
  // still-open fields. Consumers surface these inline rather than collapse
  // them into the single `message` line.
  readonly gaps?: OriginGap[];
  constructor(
    status: number,
    url: string,
    serverMessage: string | null,
    code?: string,
    errorId?: string,
    detail?: IngestErrorDetail,
  ) {
    super(status, url, code ?? null, errorId ?? null, detail ?? null);
    this.name = "IngestApiError";
    this.serverMessage = serverMessage;
    if (serverMessage !== null) this.message = serverMessage;
    this.reason = detail?.reason;
    this.existingSlug = detail?.slug;
    this.suggestedSlug = detail?.suggested_slug;
    this.draftId = detail?.draft_id;
    this.gaps = detail?.gaps;
  }
}

// What a write surface says when the server gave no sentence of its own. A transient write may
// have landed before the connection dropped, so that sentence never claims nothing changed.
const KIND_SENTENCE: Record<FailureKind, string> = {
  transient: "Could not reach the server — the change may not have been applied.",
  auth: "Your session has ended — sign in again.",
  denied: "This account is not allowed to do that.",
  gone: "What this acts on no longer exists.",
  invalid: "The server refused the request as malformed.",
};

// The one operator sentence for a failed write: the server's own where the envelope carried one,
// else the kind's. Never a raw status line, the browser's network text, or a sentence rebuilt here
// from the envelope's details — a refusal's REMEDY is known only where it was decided, so an
// ingress renders what the server said.
export function operatorMessage(e: unknown, kind: FailureKind): string {
  if (!(e instanceof IngestApiError) || e.serverMessage === null) return KIND_SENTENCE[kind];
  if (e.suggestedSlug) return `${e.serverMessage} Suggested slug: ${e.suggestedSlug}.`;
  return e.serverMessage;
}

export async function throwApiError(r: Response): Promise<never> {
  let message: string | null = null;
  let code: string | undefined;
  let errorId: string | undefined;
  let detail: IngestErrorDetail | undefined;
  try {
    // The API serializes every error to the flat ErrorEnvelope declared in
    // docs/specs/api-openapi.yaml — `{error, message, details?}` at the top level.
    const body = (await r.json()) as {
      error?: string;
      error_id?: unknown;
      message?: string;
      details?: IngestErrorDetail;
    };
    if (body?.message) {
      message = body.message;
      code = body.error;
      detail = body.details;
    }
    if (typeof body?.error_id === "string") errorId = body.error_id;
  } catch {
    /* status-only message */
  }
  throw new IngestApiError(r.status, r.url, message, code, errorId, detail);
}
