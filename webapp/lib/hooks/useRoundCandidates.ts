"use client";
// The leaf cycle's per-round rows, computed once and grouped (`groupByRound`) so the
// sample-scoped surfaces — `ScoringInspector` and `RoundSamplesView` — share one memoized
// list per `dash` snapshot. Components ask this hook rather than importing the derivation,
// which is what guarantees the merge runs once.
//
// The candidates card's bars do NOT read this: they plot the served tree's children. See
// `derivations/round-candidates.ts` for why both exist and why they cannot diverge.

import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { roundCandidates, groupByRound } from "@/lib/derivations";
import type { ElectedRow, RoundCandidates } from "@/lib/types";

interface RoundCandidatesHookState {
  all: ElectedRow[];
  byRound: RoundCandidates;
}

export function useRoundCandidates(): RoundCandidatesHookState {
  const { dash } = useCycleStream();
  return useMemo(() => {
    const all = roundCandidates(dash);
    return { all, byRound: groupByRound(all) };
  }, [dash]);
}
