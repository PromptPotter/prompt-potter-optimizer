"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { failureKind, operatorMessage, type FailureKind } from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { reportIncident } from "@/lib/diagnostics";
import {
  cachedRead,
  dropCachedRead,
  readName,
  readThrough,
  watchRead,
  type ReadDescriptor,
} from "@/lib/read-cache";

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
  // Idle until the session is confirmed; a 401 re-probes it.
  auth?: boolean;
  // `kept` then carries the PRIOR id's read under an `invalid`.
  survive?: "invalid";
  // A 404 is this read's ordinary empty answer: no incident, and the poll goes on.
  expects?: "gone";
  intervalMs?: number;
  onGone?: () => void;
}

export const GONE_CONFIRM_LIMIT = 3;

type Settled<T> = { stamp: number } & ({ ok: true; data: T } | { ok: false; failure: ReadFailure });

interface Held<T> {
  outcome: Settled<T> | null;
  last: { stamp: number; data: T } | null;
}

const IDLE = { status: "idle" } as const;

export function readyData<T>(read: ReadResult<T>): T | null {
  return read.status === "ready" ? read.data : null;
}

export function shownData<T>(read: ReadResult<T>): T | null {
  if (read.status === "ready") return read.data;
  return read.status === "idle" ? null : read.kept;
}

function landed<T>(stamp: number, data: T): Held<T> {
  return { outcome: { stamp, ok: true, data }, last: { stamp, data } };
}

export function useRead<T>(read: ReadDescriptor<T> | null, opts: ReadOptions = {}): ReadResult<T> {
  const { auth = false, survive, expects, intervalMs, onGone } = opts;
  const { authed, onAuthError } = useAuthGate();
  const readId = read !== null && (!auth || authed) ? read.id : null;

  const [generation, setGeneration] = useState({ id: readId, n: 0 });
  const [held, setHeld] = useState<Held<T>>(() => {
    const hit = readId === null ? null : cachedRead<T>(readId);
    return hit ? landed(0, hit.data) : { outcome: null, last: null };
  });
  const [goneAt, setGoneAt] = useState<number | null>(null);
  if (generation.id !== readId) {
    const n = generation.n + 1;
    setGeneration({ id: readId, n });
    const hit = readId === null ? null : cachedRead<T>(readId);
    if (hit) setHeld(landed(n, hit.data));
  }
  const gen = generation.n;

  const issueRef = useRef<ReadDescriptor<T> | null>(null);
  const genRef = useRef(gen);
  const onGoneRef = useRef(onGone);
  const missesRef = useRef({ stamp: gen, n: 0 });
  useEffect(() => {
    issueRef.current = readId !== null ? read : null;
    genRef.current = gen;
    onGoneRef.current = onGone;
  });

  const run = useCallback(
    async (stamp: number, signal: AbortSignal): Promise<void> => {
      const issue = issueRef.current;
      if (!issue) return;
      const misses = missesRef.current.stamp === stamp ? missesRef.current.n : 0;
      try {
        const data = await readThrough(issue.id, issue.load, signal);
        if (signal.aborted || genRef.current !== stamp) return;
        missesRef.current = { stamp, n: 0 };
        // A 304 hands back the body already held, and must re-render no one.
        setHeld((prev) =>
          prev.outcome?.ok && prev.outcome.stamp === stamp && prev.outcome.data === data
            ? prev
            : landed(stamp, data),
        );
      } catch (e) {
        if (signal.aborted || genRef.current !== stamp) return;
        if (auth) onAuthError(e);
        const kind = failureKind(e);
        if (kind !== expects) reportIncident(e, { surface: readName(issue.id), address: issue.id });
        if (kind === "gone" && expects !== "gone") {
          // A body for an address that no longer exists must not paint the next mount.
          dropCachedRead(issue.id);
          missesRef.current = { stamp, n: misses + 1 };
          if (misses + 1 === GONE_CONFIRM_LIMIT) {
            setGoneAt(stamp);
            onGoneRef.current?.();
          }
        } else {
          missesRef.current = { stamp, n: 0 };
        }
        const failure = { kind, message: operatorMessage(e, kind) };
        setHeld((prev) => ({ ...prev, outcome: { stamp, ok: false, failure } }));
      }
    },
    [auth, onAuthError, expects],
  );

  const active = readId !== null;
  useEffect(() => {
    if (!active) return;
    const ac = new AbortController();
    void run(gen, ac.signal);
    return () => ac.abort();
  }, [active, gen, run]);

  const stopped = goneAt === gen;
  useEffect(() => {
    if (readId === null || stopped) return;
    const ac = new AbortController();
    const unwatch = watchRead({
      id: readId,
      intervalMs: intervalMs ?? null,
      fire: () => void run(genRef.current, ac.signal),
    });
    return () => {
      unwatch();
      ac.abort();
    };
  }, [readId, stopped, intervalMs, run]);

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
