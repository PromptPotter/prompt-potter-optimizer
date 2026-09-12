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

import { ApiError } from "./client";
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
  backend_type?: string;
  backend_url?: string;
  draft_id?: string;
  gaps?: OriginGap[];
};
export class IngestApiError extends ApiError {
  readonly reason?: string;
  // `slug_collision` (409): the existing dataset name + a free suggestion.
  readonly existingSlug?: string;
  readonly suggestedSlug?: string;
  readonly backendType?: string;
  readonly backendUrl?: string;
  readonly draftId?: string;
  // Populated on `origin_incomplete` (422) — the deterministic checklist's
  // still-open fields. Consumers surface these inline rather than collapse
  // them into the single `message` line.
  readonly gaps?: OriginGap[];
  constructor(
    status: number,
    url: string,
    message: string,
    code?: string,
    errorId?: string,
    detail?: IngestErrorDetail,
  ) {
    super(status, url, code ?? null, errorId ?? null, detail ?? null);
    this.name = "IngestApiError";
    // `ApiError`'s message is `${status} ${url}`, which serves a log line; a write surface
    // renders the server's own sentence instead.
    this.message = message;
    this.reason = detail?.reason;
    this.existingSlug = detail?.slug;
    this.suggestedSlug = detail?.suggested_slug;
    this.backendType = detail?.backend_type;
    this.backendUrl = detail?.backend_url;
    this.draftId = detail?.draft_id;
    this.gaps = detail?.gaps;
  }

  // Operator-facing error message. Every consumer that catches an
  // IngestApiError renders via this method instead of reimplementing the
  // per-error-code translation. Static `from` helper wraps unknown errors so
  // call sites are one line:
  //   setError(IngestApiError.toOperatorMessage(e))
  toOperatorMessage(): string {
    if (this.code === "backend_unreachable") {
      const where = this.backendUrl ? ` at ${this.backendUrl}` : "";
      const what = this.backendType ? ` ‘${this.backendType}’` : "";
      return `Backend${what}${where} is not running. Start the backend and try again.`;
    }
    if (this.suggestedSlug) {
      return `${this.message} Suggested slug: ${this.suggestedSlug}.`;
    }
    return this.message;
  }

  // One-liner for ``catch`` blocks. Renders unknown errors via their
  // standard message; ``IngestApiError`` instances route through
  // ``.toOperatorMessage()``.
  static toOperatorMessage(e: unknown): string {
    if (e instanceof IngestApiError) return e.toOperatorMessage();
    return e instanceof Error ? e.message : String(e);
  }
}
export async function throwApiError(r: Response): Promise<never> {
  let message = `${r.status} ${r.statusText}`;
  let code: string | undefined;
  let errorId: string | undefined;
  let detail: IngestErrorDetail | undefined;
  try {
    // The API serializes every error to the flat ErrorEnvelope declared in
    // docs/specs/api-openapi.yaml — `{error, message, details?}` at the top
    // level (no `detail` wrapper). The `detail` fallback only catches Starlette's
    // built-in 404/422 for genuinely unmatched routes, which we never call.
    const body = (await r.json()) as {
      error?: string;
      error_id?: unknown;
      message?: string;
      details?: IngestErrorDetail;
      detail?: string;
    };
    if (body?.message) {
      message = body.message;
      code = body.error;
      detail = body.details;
    } else if (typeof body?.detail === "string") {
      message = body.detail;
    }
    // The trace handle rides the envelope whichever arm above claimed the message.
    if (typeof body?.error_id === "string") errorId = body.error_id;
  } catch {
    /* status-only message */
  }
  throw new IngestApiError(r.status, r.url, message, code, errorId, detail);
}
