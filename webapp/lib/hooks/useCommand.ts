// Never retries: each send mints a fresh idempotency key, so a retry is a second command.

import { useRef, useState } from "react";
import { ApiError, commandMessage, failureKind, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/state/auth";
import { reportIncident } from "@/lib/diagnostics";
import { beginWrite, invalidateReads } from "@/lib/read-cache";

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
    /**
     * The reads, by name, the write moves: in flight it is `writing` on them, answered (landed or
     * refused) it re-asks them. Absent is every read; `false` none: its caller reads back what it
     * moves (consent re-probes the session, a logout navigates), or it moves nothing (a preview).
     */
    revalidate?: false | readonly string[];
    /** The subject's identity; a change clears `failure`. */
    scope?: string | null;
  } = {},
): CommandSlot<V> {
  const { revalidate = null, scope = null } = opts;
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
    // Until answered, the reads it names see `writing`: the draft rests under its own write, whose
    // answer re-asks it.
    const answered = revalidate === false ? () => {} : beginWrite(revalidate);
    const reask = () => {
      if (revalidate === null) invalidateReads();
      else if (revalidate !== false) for (const name of revalidate) invalidateReads(name);
    };
    try {
      const value = await send();
      then?.(value);
      reask();
      return { ok: true, value };
    } catch (e) {
      if (isAbort(e)) return { ok: false, failure: null };
      onAuthError(e);
      reportIncident(e, { surface: `${surface}:${verb}` });
      const kind = failureKind(e);
      // Whatever refused it, the sends before the refused one may have moved what the write
      // names (a paid turn books each it started), and the client cannot tell which ran.
      reask();
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
    } finally {
      answered();
      inFlight.current = false;
      setPending(null);
    }
  };

  return { pending, failure, run, clear: () => setFailure(null) };
}
