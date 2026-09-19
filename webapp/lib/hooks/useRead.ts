"use client";
// The one keyed read: a `null` spec parks it, and a result renders only for the key generation
// that issued it. `kept` is the last good read a caller may hold on screen
// (`webapp/CLAUDE.md` § Failure handling).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureKind, operatorMessage, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { reportIncident } from "@/lib/diagnostics";
import { usePoll } from "./usePoll";

export interface ReadSpec<T> {
  key: string;
  fetch: (signal: AbortSignal) => Promise<T>;
}

export interface ReadFailure {
  kind: FailureKind;
  message: string;
}

export type ReadResult<T> =
  | { status: "idle" }
  | { status: "loading"; kept: T | null }
  | { status: "ready"; data: T }
  | { status: "failed"; failure: ReadFailure; kept: T | null };

export interface ReadOptions {
  // Diagnostics surface name for the incident ring.
  surface: string;
  // Idle until the session is confirmed (`frontend-surface-contract.md::I5`); a 401 re-probes it.
  auth?: boolean;
  // A rejected query is the operator's own input, so `kept` then carries the prior key's read.
  survive?: "invalid";
  // Present ⇒ the key is re-read on this cadence through `usePoll`, which pauses a hidden tab.
  intervalMs?: number;
  revalidateOn?: number;
}

type Settled<T> = { stamp: number } & ({ ok: true; data: T } | { ok: false; failure: ReadFailure });

interface Held<T> {
  outcome: Settled<T> | null;
  last: { stamp: number; data: T } | null;
}

const IDLE = { status: "idle" } as const;

export function readyData<T>(read: ReadResult<T>): T | null {
  return read.status === "ready" ? read.data : null;
}

export function useRead<T>(spec: ReadSpec<T> | null, opts: ReadOptions): ReadResult<T> {
  const { surface, auth = false, survive, intervalMs, revalidateOn = 0 } = opts;
  const { authed, onAuthError } = useAuthGate();
  // One value claims the read and issues it.
  const activeKey = spec !== null && (!auth || authed) ? spec.key : null;

  const [generation, setGeneration] = useState({ key: activeKey, n: 0 });
  if (generation.key !== activeKey) setGeneration({ key: activeKey, n: generation.n + 1 });
  const gen = generation.n;

  const fetchRef = useRef(spec?.fetch);
  const genRef = useRef(gen);
  useEffect(() => {
    fetchRef.current = spec?.fetch;
    genRef.current = gen;
  });

  const [held, setHeld] = useState<Held<T>>({ outcome: null, last: null });

  const run = useCallback(
    async (stamp: number, signal: AbortSignal): Promise<void> => {
      const fetch = fetchRef.current;
      if (!fetch) return;
      try {
        const data = await fetch(signal);
        if (signal.aborted || genRef.current !== stamp) return;
        setHeld({ outcome: { stamp, ok: true, data }, last: { stamp, data } });
      } catch (e) {
        if (signal.aborted || genRef.current !== stamp) return;
        if (auth) onAuthError(e);
        reportIncident(e, { surface });
        const kind = failureKind(e);
        const failure = { kind, message: operatorMessage(e, kind) };
        setHeld((prev) => ({ ...prev, outcome: { stamp, ok: false, failure } }));
      }
    },
    [auth, onAuthError, surface],
  );

  const active = activeKey !== null;
  const polled = intervalMs !== undefined;
  useEffect(() => {
    if (!active || polled) return;
    const ac = new AbortController();
    void run(gen, ac.signal);
    return () => ac.abort();
  }, [active, polled, gen, run]);

  usePoll((signal) => run(genRef.current, signal), {
    intervalMs: intervalMs ?? 0,
    enabled: active && polled,
    revalidateOn: revalidateOn + gen,
  });

  return useMemo((): ReadResult<T> => {
    if (!active) return IDLE;
    const own = held.last?.stamp === gen ? held.last.data : null;
    const outcome = held.outcome?.stamp === gen ? held.outcome : null;
    if (outcome === null) return { status: "loading", kept: own };
    if (outcome.ok) return { status: "ready", data: outcome.data };
    const carried = outcome.failure.kind === survive ? (held.last?.data ?? null) : null;
    return { status: "failed", failure: outcome.failure, kept: own ?? carried };
  }, [active, gen, held, survive]);
}
