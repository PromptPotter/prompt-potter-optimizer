"use client";
// Never retries: each send mints a fresh idempotency key, so a retry is a second command.

import { useRef, useState } from "react";
import { ApiError, failureKind, operatorMessage, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/state/auth";
import { reportIncident } from "@/lib/diagnostics";
import { invalidateReads } from "@/lib/read-cache";

export interface CommandFailure {
  kind: FailureKind;
  message: string;
  errorId: string | null;
  // The refusal's served `code` and `details`, for a caller whose next step is the refusal itself.
  code: string | null;
  details: Record<string, unknown> | null;
}

// `failure` is null for an abort and for a send refused while another is in flight.
type CommandResult<R> = { ok: true; value: R } | { ok: false; failure: CommandFailure | null };

interface CommandSlot<V extends string> {
  pending: V | null;
  failure: (CommandFailure & { verb: V }) | null;
  /** Never throws. A send while another is in flight is refused as `{ ok: false, failure: null }`. */
  run<R>(verb: V, send: () => Promise<R>, then?: (r: R) => void): Promise<CommandResult<R>>;
  clear(): void;
}

const isAbort = (e: unknown) =>
  (e instanceof DOMException || e instanceof Error) && e.name === "AbortError";

export function useCommand<V extends string>(
  surface: string,
  opts: {
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
    if (inFlight.current) return { ok: false, failure: null };
    inFlight.current = true;
    setPending(verb);
    setFailure(null);
    try {
      const value = await send();
      then?.(value);
      if (revalidate) invalidateReads();
      return { ok: true, value };
    } catch (e) {
      if (isAbort(e)) return { ok: false, failure: null };
      onAuthError(e);
      reportIncident(e, { surface: `${surface}:${verb}` });
      const kind = failureKind(e);
      const api = e instanceof ApiError ? e : null;
      const f: CommandFailure = {
        kind,
        message: operatorMessage(e, kind),
        errorId: api?.errorId ?? null,
        code: api?.code ?? null,
        details: api?.details ?? null,
      };
      const described = { ...f, message: describe?.(f, verb) ?? f.message };
      setFailure({ ...described, verb });
      return { ok: false, failure: described };
    } finally {
      inFlight.current = false;
      setPending(null);
    }
  };

  return { pending, failure, run, clear: () => setFailure(null) };
}
