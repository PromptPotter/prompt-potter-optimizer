// The write surface's failure vocabulary. `IngestApiError` is raised by EVERY write path, and must
// stay an `ApiError`, or `failureKind` classifies each write failure as `transient`.

import { ApiError, type FailureKind } from "./client";
import type { OriginGap } from "./draft-types";

export function mintIdempotencyKey(): string {
  return crypto.randomUUID();
}
// A type alias rather than an interface, so it satisfies `ApiError.details`'
// `Record<string, unknown>`: an interface carries no implicit index signature.
export type IngestErrorDetail = {
  reason?: string;
  slug?: string;
  suggested_slug?: string;
  draft_id?: string;
  gaps?: OriginGap[];
};
export class IngestApiError extends ApiError {
  readonly serverMessage: string | null;
  readonly reason?: string;
  readonly existingSlug?: string;
  readonly suggestedSlug?: string;
  readonly draftId?: string;
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

// A transient write may have landed before the connection dropped, so its sentence never claims
// nothing changed.
const KIND_SENTENCE: Record<FailureKind, string> = {
  transient: "Could not reach the server — the change may not have been applied.",
  auth: "Your session has ended — sign in again.",
  denied: "This account is not allowed to do that.",
  gone: "What this acts on no longer exists.",
  invalid: "The server refused the request as malformed.",
};

// Never rebuild a sentence from the envelope's details: a refusal's remedy is known only where the
// server decided it.
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
