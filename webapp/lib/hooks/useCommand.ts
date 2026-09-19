"use client";
// The one write path. Never retries — each send mints a fresh idempotency key, so a retry is a
// second command — and never reports an address gone: a write's 404 is a missing capability.

import { useRef, useState } from "react";
import { ApiError, failureKind, operatorMessage, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { reportIncident } from "@/lib/diagnostics";
import { bumpRevalidation } from "@/lib/revalidate";

export interface CommandFailure {
  kind: FailureKind;
  message: string;
  errorId: string | null;
}

type CommandResult<R> = { ok: true; value: R } | { ok: false };

export interface CommandSlot<V extends string> {
  pending: V | null;
  failure: (CommandFailure & { verb: V }) | null;
  /** Never throws. A send while another is in flight is refused as `{ ok: false }`. */
  run<R>(verb: V, send: () => Promise<R>, then?: (r: R) => void): Promise<CommandResult<R>>;
  clear(): void;
}

const isAbort = (e: unknown) =>
  (e instanceof DOMException || e instanceof Error) && e.name === "AbortError";

export function useCommand<V extends string>(
  surface: string,
  opts: {
    /** Per-kind wording; `null` keeps the default sentence. */
    describe?: (f: CommandFailure, verb: V) => string | null;
    /** `false` for a write no poll reads back (preferences, consent, logout). */
    revalidate?: boolean;
    /** The subject's identity; a change clears `failure`. */
    scope?: string | null;
  } = {},
): CommandSlot<V> {
  const { describe, revalidate = true, scope = null } = opts;
  const { onAuthError } = useAuthGate();
  const [pending, setPending] = useState<V | null>(null);
  const [failure, setFailure] = useState<CommandSlot<V>["failure"]>(null);
  const inFlight = useRef(false);

  const [prevScope, setPrevScope] = useState(scope);
  if (scope !== prevScope) {
    setPrevScope(scope);
    setFailure(null);
  }

  const run = async <R>(
    verb: V,
    send: () => Promise<R>,
    then?: (r: R) => void,
  ): Promise<CommandResult<R>> => {
    if (inFlight.current) return { ok: false };
    inFlight.current = true;
    setPending(verb);
    setFailure(null);
    try {
      const value = await send();
      then?.(value);
      if (revalidate) bumpRevalidation();
      return { ok: true, value };
    } catch (e) {
      if (!isAbort(e)) {
        onAuthError(e);
        reportIncident(e, { surface: `${surface}:${verb}` });
        const kind = failureKind(e);
        const f: CommandFailure = {
          kind,
          message: operatorMessage(e, kind),
          errorId: e instanceof ApiError ? e.errorId : null,
        };
        setFailure({ ...f, message: describe?.(f, verb) ?? f.message, verb });
      }
      return { ok: false };
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  };

  return { pending, failure, run, clear: () => setFailure(null) };
}
