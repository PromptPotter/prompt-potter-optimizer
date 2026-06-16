"use client";
// The candidate-spine hook. Wraps `roundCandidates` /
// `roundCandidatesByRound` so the same memoized list backs every
// surface that lists or selects candidates (FitnessPanel, LineageTree,
// RoundSamplesView, RoundTabsStrip). Components don't import the
// derivation directly — they ask this hook, which guarantees the
// computation runs once per `dash` snapshot.

import { useMemo } from "react";
import { useCycleStream } from "@/lib/poll";
import { roundCandidates, roundCandidatesByRound } from "@/lib/derivations";
import type { CandidateRow, RoundCandidates } from "@/lib/types";

export interface RoundCandidatesHookState {
  all: CandidateRow[];
  byRound: RoundCandidates;
}

export function useRoundCandidates(): RoundCandidatesHookState {
  const { dash } = useCycleStream();
  return useMemo(
    () => ({ all: roundCandidates(dash), byRound: roundCandidatesByRound(dash) }),
    [dash],
  );
}
