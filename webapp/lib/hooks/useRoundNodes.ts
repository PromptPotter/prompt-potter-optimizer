"use client";
// The single "which round's node blocks am I showing" resolver for the optimizer
// card — the canvas and its drill-in panel now read one answer.
//
// They used to disagree: the canvas hardcoded `dash.current_round.nodes` (LIVE
// ONLY) while the node detail resolved the round through `useEffectiveRound()`
// and fetched the audit twin. Picking round 2 while round 5 ran lit up round 5's
// dots and pulse above round 2's I/O.
//
// The source is SELECTED, never merged (the no-stitch rule in webapp/CLAUDE.md
// § Display-data sources):
//   live round       → `dash.current_round.nodes` (inline, no fetch)
//   historical round → the AUDIT TWIN, `.runtime/cache/rounds/round_NNNN.json`
//                      (the round *document* carries no `nodes` block at all)

import { useMemo } from "react";
import { useDashboard } from "./useDashboard";
import { useEffectiveRound } from "./useEffectiveRound";
import { useRoundAudit } from "./useRoundFile";
import { isLiveRound } from "./useRoundSource";
import { useWorkspace } from "@/lib/workspace";
import type { NodeBlock } from "@/lib/types";

const EMPTY: Record<string, NodeBlock> = {};

export interface RoundNodes {
  round: number | null;
  // The resolved round IS the in-flight, not-yet-closed round. A DIFFERENT
  // question than `useEffectiveRound().isLiveView` ("is the operator following
  // live"): this one asks "does a round file exist for it yet" — which is what
  // both the source guard and the active-node pulse need.
  isLiveRound: boolean;
  nodes: Record<string, NodeBlock>;
  loading: boolean;
}

export function useRoundNodes(): RoundNodes {
  const { dash } = useDashboard();
  const { viewedPath } = useWorkspace();
  const { round } = useEffectiveRound();
  const live = isLiveRound(dash, round);
  const { doc, loading } = useRoundAudit(live ? null : viewedPath, round);
  const nodes = useMemo(() => {
    if (round == null) return EMPTY;
    if (live) return (dash?.current_round.nodes ?? EMPTY) as Record<string, NodeBlock>;
    return doc?.nodes ?? EMPTY;
  }, [round, live, dash, doc]);
  return { round, isLiveRound: live, nodes, loading };
}
