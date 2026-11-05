import { ApiError, type FailureKind } from "./client";

export function mintIdempotencyKey(): string {
  return crypto.randomUUID();
}

// True of a read and a write alike; what a dropped write may have done is `commandMessage`'s to say.
const KIND_SENTENCE: Record<FailureKind, string> = {
  transient: "Could not reach the server.",
  throttled: "Too many requests from this account — try again shortly.",
  auth: "Your session has ended — sign in again.",
  denied: "This account is not allowed to do that.",
  gone: "What this acts on no longer exists.",
  invalid: "The server refused the request as malformed.",
  refused: "The server declined the request.",
};

// Never rebuild a sentence from the envelope's details: only the server knows a refusal's remedy.
export function operatorMessage(e: unknown, kind: FailureKind): string {
  if (!(e instanceof ApiError) || e.serverMessage === null) return KIND_SENTENCE[kind];
  return e.serverMessage;
}

// A transient write may have landed before the connection dropped, and a resend mints a fresh
// idempotency key: never claim nothing changed.
export function commandMessage(e: unknown, kind: FailureKind): string {
  const said = operatorMessage(e, kind);
  return kind === "transient" ? `${said} Anything you sent may not have been applied.` : said;
}
