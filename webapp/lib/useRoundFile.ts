"use client";
// Lazy per-round file fetch. Deep audit surfaces (FreqChart bucket data,
// ScoringInspector composite + hits, OptimizerNodeDetail node blocks) need
// one round_NNNN.json at a time — not the full eager array. The summary
// surfaces (FitnessPanel, TrendChart, TopStrip sparkline, LineageTree) read
// `dash.rounds[]` directly and never hit this hook.
//
// Pattern matches `useDatasetPreview`: the loaded data is stamped with the
// `(campaignId, cycleId, round)` key it was fetched for, and the hook
// returns EMPTY until the stamp matches the current key. No stale-frame
// flash on key change, no manual reset needed by the caller.

import { useEffect, useState } from "react";
import { fetchCycleFile } from "./api";
import type { RoundFileDoc } from "./poll";

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

function roundKey(
  campaignId: string | null,
  cycleId: string | null,
  round: number | null,
): string | null {
  if (!campaignId || !cycleId || round == null) return null;
  return `${campaignId}\x1f${cycleId}\x1f${round}`;
}

function roundPath(round: number): string {
  return `rounds/round_${String(round).padStart(4, "0")}.json`;
}

export function useRoundFile(
  campaignId: string | null,
  cycleId: string | null,
  round: number | null,
): UseRoundFileState {
  const key = roundKey(campaignId, cycleId, round);
  const [loaded, setLoaded] = useState<Loaded>({ key: null, doc: null });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!campaignId || !cycleId || round == null) return;
    const ac = new AbortController();
    (async () => {
      try {
        const r = await fetchCycleFile(
          campaignId,
          cycleId,
          "cycle",
          roundPath(round),
          ac.signal,
        );
        if (ac.signal.aborted) return;
        const doc = r.content ? (JSON.parse(r.content) as RoundFileDoc) : null;
        setLoaded({ key, doc });
        setError(null);
      } catch (e) {
        if (ac.signal.aborted) return;
        setLoaded({ key, doc: null });
        setError((e as Error).message);
      }
    })();
    return () => ac.abort();
  }, [campaignId, cycleId, round, key]);

  // Pure derivation — until the loaded stamp matches the current key, the
  // hook returns EMPTY (loading=true if we're actually fetching, false when
  // no key is requested). A unit/round switch can never surface the prior
  // fetch's payload under the new header.
  if (key == null) return EMPTY;
  if (loaded.key !== key) return { doc: null, loading: true, error: null };
  return { doc: loaded.doc, loading: false, error };
}
