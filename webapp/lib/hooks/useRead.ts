"use client";
// The one keyed read: a `null` spec parks it, and a result renders only for the key generation
// that issued it. `kept` is the last good read a caller may hold on screen
// (`webapp/CLAUDE.md` § Failure handling).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureKind, operatorMessage, type Conditional, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { reportIncident } from "@/lib/diagnostics";
import { cachedRead, dropCachedRead, readThrough, type ReadLoad } from "@/lib/read-cache";
import { usePoll } from "./usePoll";

export type ReadSpec<T> = { key: string } & (
  | { fetch: (signal: AbortSignal) => Promise<T> }
  // Sent the held body's validator; a 304 keeps that body.
  | { conditional: ReadLoad<T> }
);

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
  surface: string;
  // Idle until the session is confirmed (`frontend-surface-contract.md::I5`); a 401 re-probes it.
  auth?: boolean;
  // A rejected query is the operator's own input, so `kept` then carries the prior key's read.
  survive?: "invalid";
  // A 404 is this read's ordinary empty answer, so it is no incident.
  expects?: "gone";
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

function loadOf<T>(spec: ReadSpec<T>): ReadLoad<T> {
  if ("conditional" in spec) return spec.conditional;
  const { fetch } = spec;
  return async (signal): Promise<Conditional<T>> => ({
    kind: "ok",
    data: await fetch(signal),
    validator: null,
  });
}

function landed<T>(stamp: number, data: T): Held<T> {
  return { outcome: { stamp, ok: true, data }, last: { stamp, data } };
}

export function useRead<T>(spec: ReadSpec<T> | null, opts: ReadOptions): ReadResult<T> {
  const { surface, auth = false, survive, expects, intervalMs, revalidateOn = 0 } = opts;
  const { authed, onAuthError } = useAuthGate();
  const activeKey = spec !== null && (!auth || authed) ? spec.key : null;
  const readId = activeKey === null ? null : `${surface}\x1f${activeKey}`;

  const [generation, setGeneration] = useState({ key: activeKey, n: 0 });
  const [held, setHeld] = useState<Held<T>>(() => {
    const hit = readId === null ? null : cachedRead<T>(readId);
    return hit ? landed(0, hit.data) : { outcome: null, last: null };
  });
  if (generation.key !== activeKey) {
    const n = generation.n + 1;
    setGeneration({ key: activeKey, n });
    const hit = readId === null ? null : cachedRead<T>(readId);
    if (hit) setHeld(landed(n, hit.data));
  }
  const gen = generation.n;

  const issueRef = useRef<{ id: string; load: ReadLoad<T> } | null>(null);
  const genRef = useRef(gen);
  useEffect(() => {
    issueRef.current = spec !== null && readId !== null ? { id: readId, load: loadOf(spec) } : null;
    genRef.current = gen;
  });

  const run = useCallback(
    async (stamp: number, signal: AbortSignal): Promise<void> => {
      const issue = issueRef.current;
      if (!issue) return;
      try {
        const data = await readThrough(issue.id, issue.load, signal);
        if (signal.aborted || genRef.current !== stamp) return;
        setHeld(landed(stamp, data));
      } catch (e) {
        if (signal.aborted || genRef.current !== stamp) return;
        if (auth) onAuthError(e);
        const kind = failureKind(e);
        if (kind !== expects) reportIncident(e, { surface });
        // A body for an address that no longer exists must not paint the next mount.
        if (kind === "gone") dropCachedRead(issue.id);
        const failure = { kind, message: operatorMessage(e, kind) };
        setHeld((prev) => ({ ...prev, outcome: { stamp, ok: false, failure } }));
      }
    },
    [auth, onAuthError, surface, expects],
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
