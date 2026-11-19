// Never retries: each send mints a fresh idempotency key, so a retry is a second command.

import { useEffect, useRef, useState } from "react";
import { ApiError, commandMessage, failureKind, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/state/auth";
import { reportIncident } from "@/lib/diagnostics";

export interface CommandFailure {
  kind: FailureKind;
  message: string;
  errorId: string | null;
  // The refusal's served `code` and `details`, for a caller whose next step is the refusal itself.
  code: string | null;
  details: Record<string, unknown> | null;
}

// `failure` is null for an abort, a refused double send and an answer whose scope moved.
type CommandResult<R> = { ok: true; value: R } | { ok: false; failure: CommandFailure | null };

interface CommandSlot<V extends string> {
  pending: V | null;
  failure: (CommandFailure & { verb: V }) | null;
  /** Never throws; a throwing `then` is the caller's incident, never a `failure`. A send while
   * another of its scope is in flight is refused as `failure: null`. */
  run<R>(verb: V, send: () => Promise<R>, then?: (r: R) => void): Promise<CommandResult<R>>;
  clear(): void;
}

const isAbort = (e: unknown) =>
  (e instanceof DOMException || e instanceof Error) && e.name === "AbortError";

const DROPPED = { ok: false, failure: null } as const;

/** `scope` is the subject's identity (an account-level slot's is `ACCOUNT`): a send's answer
 * lands only on the scope it was sent from. */
export function useCommand<V extends string>(surface: string, scope: string | null): CommandSlot<V> {
  const { onAuthError } = useAuthGate();
  const [pending, setPending] = useState<V | null>(null);
  const [failure, setFailure] = useState<CommandSlot<V>["failure"]>(null);
  // The send in flight and the scope it was sent from; a moved scope frees the slot.
  const sent = useRef<{ scope: string | null } | null>(null);
  const scopeNow = useRef(scope);
  useEffect(() => {
    scopeNow.current = scope;
  });

  const [prevScope, setPrevScope] = useState(scope);
  if (scope !== prevScope) {
    setPrevScope(scope);
    setPending(null);
    setFailure(null);
  }

  // Frees the slot; true when the answer is still the viewed scope's, else it is dropped as an abort.
  const settle = (mine: { scope: string | null }) => {
    const lands = sent.current === mine && scopeNow.current === mine.scope;
    if (sent.current === mine) sent.current = null;
    if (lands) setPending(null);
    return lands;
  };

  const run: CommandSlot<V>["run"] = async (verb, send, then) => {
    if (sent.current?.scope === scopeNow.current) return DROPPED;
    const mine = (sent.current = { scope: scopeNow.current });
    setPending(verb);
    setFailure(null);
    let value;
    // What the write moved, the feed says: the dispatcher files it on the account's log.
    try {
      value = await send();
    } catch (e) {
      if (!settle(mine) || isAbort(e)) return DROPPED;
      onAuthError(e);
      reportIncident(e, { surface: `${surface}:${verb}` });
      const kind = failureKind(e);
      const api = e instanceof ApiError ? e : null;
      const f: CommandFailure = {
        kind,
        message: commandMessage(e, kind),
        errorId: api?.errorId ?? null,
        code: api?.code ?? null,
        details: api?.details ?? null,
      };
      setFailure({ ...f, verb });
      return { ok: false, failure: f };
    }
    // Settled before `then`, so a send it chains is admitted, never dropped as a double press.
    if (!settle(mine)) return DROPPED;
    try {
      then?.(value);
    } catch (e) {
      reportIncident(e, { surface: `${surface}:${verb}` });
    }
    return { ok: true, value };
  };

  return { pending, failure, run, clear: () => setFailure(null) };
}
