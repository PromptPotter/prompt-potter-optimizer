"use client";
// Lazy per-round file fetch. Deep audit surfaces (FreqChart bucket data,
// ScoringInspector composite + hits, OptimizerNodeDetail node blocks) need
// one round_NNNN.json at a time — not the full eager array. The summary
// surfaces (FitnessPanel, TrendChart, TopStrip sparkline, LineageTree) read
// `dash.rounds[]` directly and never hit this hook.
//
// Addressed by the viewed CYCLE PATH, not bare `(campaign, cycle)` ids: the
// round file follows the LEAF hop the dashboard shows, so an L4 inner loop's
// `rounds/round_NNNN.json` reads from the inner cycle's dir (via `?descend=`)
// instead of the outer root's empty `rounds/`. `fetchCycleFileByPath` mirrors
// `fetchDashboardByPath` — the same seam the live poll already rides.
//
// Pattern matches `useDatasetPreview`: the loaded data is stamped with the
// `(path, round)` key it was fetched for, and the hook returns EMPTY until the
// stamp matches the current key. No stale-frame flash on key change, no manual
// reset needed by the caller. The key is the ENCODED path string (not the array
// identity, which is fresh each render while following), so the fetch effect
// re-runs only when the viewed cycle or round actually changes.

import { useEffect, useRef, useState } from "react";
import { fetchCycleFileByPath } from "../api";
import { encodeCyclePath, type CyclePath } from "../ids";
import type { RoundFileDoc } from "../poll";

export interface UseRoundFileState {
  doc: RoundFileDoc | null;
  loading: boolean;
  error: string | null;
}

interface Loaded {
  key: string | null;
  doc: RoundFileDoc | null;
}

const EMPTY: UseRoundFileState = { doc: null, loading: false, error: null };

function roundKey(path: CyclePath | null, round: number | null): string | null {
  if (!path || round == null) return null;
  return `${encodeCyclePath(path)}\x1f${round}`;
}

function roundPath(round: number): string {
  return `rounds/round_${String(round).padStart(4, "0")}.json`;
}

export function useRoundFile(
  path: CyclePath | null,
  round: number | null,
): UseRoundFileState {
  const key = roundKey(path, round);
  const [loaded, setLoaded] = useState<Loaded>({ key: null, doc: null });
  const [error, setError] = useState<string | null>(null);
  // Keep the current path/round for the fetch without depending on the array's
  // per-render identity — the fetch effect keys on the stable `key` string
  // alone. The refs are synced in an effect (never during render) and, being
  // declared first, are current before the keyed fetch effect reads them.
  const pathRef = useRef<CyclePath | null>(path);
  const roundRef = useRef<number | null>(round);
  useEffect(() => {
    pathRef.current = path;
    roundRef.current = round;
  });

  useEffect(() => {
    const p = pathRef.current;
    const r = roundRef.current;
    if (!p || r == null) return;
    const ac = new AbortController();
    (async () => {
      try {
        const resp = await fetchCycleFileByPath(p, "cycle", roundPath(r), ac.signal);
        if (ac.signal.aborted) return;
        const doc = resp.content ? (JSON.parse(resp.content) as RoundFileDoc) : null;
        setLoaded({ key, doc });
        setError(null);
      } catch (e) {
        if (ac.signal.aborted) return;
        setLoaded({ key, doc: null });
        setError((e as Error).message);
      }
    })();
    return () => ac.abort();
  }, [key]);

  // Pure derivation — until the loaded stamp matches the current key, the
  // hook returns EMPTY (loading=true if we're actually fetching, false when
  // no key is requested). A unit/round switch can never surface the prior
  // fetch's payload under the new header.
  if (key == null) return EMPTY;
  if (loaded.key !== key) return { doc: null, loading: true, error: null };
  return { doc: loaded.doc, loading: false, error };
}
