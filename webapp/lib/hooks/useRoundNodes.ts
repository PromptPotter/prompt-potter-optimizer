"use client";
// The single "which round's node blocks am I showing" resolver for the optimizer card — the
// canvas and its drill-in panel read one answer, so they cannot light up different rounds.
//
// The source is SELECTED, never merged (the no-stitch rule in webapp/CLAUDE.md
// § Display-data sources):
//   the round `current_round` describes → `dash.current_round.nodes` (inline, no fetch)
//   any other round                     → the AUDIT TWIN, `.runtime/cache/rounds/round_NNNN.json`
//                                         (the round *document* carries no `nodes` block at all)
//
// The selector is plain equality against `current_round.round`. Asking "has this round closed
// into `rounds[]`" instead swapped to the audit twin the instant a round closed, so the whole
// post-round escalation window — `l2_context` running for minutes, writing into
// `current_round.nodes` — was read out of a file flushed before any of it happened.

import { useMemo } from "react";
import { useDashboard } from "./useDashboard";
import { useEffectiveRound } from "./useEffectiveRound";
import { useRoundAudit } from "./useRoundFile";
import { useWorkspace } from "@/lib/workspace";
import type { NodeBlock } from "@/lib/types";

const EMPTY: Record<string, NodeBlock> = {};

export interface RoundNodes {
  round: number | null;
  // The resolved round IS the one `current_round` describes. A DIFFERENT question than
  // `useEffectiveRound().isLiveView` ("is the operator following live"): this one asks
  // "does the live block already hold this round's nodes", which is what both the source
  // selection and the active-node pulse need.
  showsCurrent: boolean;
  nodes: Record<string, NodeBlock>;
  // True while the audit twin is in flight. Surfaced rather than swallowed: an empty map and
  // an unfinished fetch render identically ("this node never fired"), and every source flip
  // spends at least one round-trip in that state.
  loading: boolean;
}

export function useRoundNodes(): RoundNodes {
  const { dash } = useDashboard();
  const { viewedPath } = useWorkspace();
  const { round } = useEffectiveRound();
  const showsCurrent = round != null && round === (dash?.current_round.round ?? null);
  const { doc, loading } = useRoundAudit(showsCurrent ? null : viewedPath, round);
  const nodes = useMemo(() => {
    if (round == null) return EMPTY;
    if (showsCurrent) return (dash?.current_round.nodes ?? EMPTY) as Record<string, NodeBlock>;
    return doc?.nodes ?? EMPTY;
  }, [round, showsCurrent, dash, doc]);
  return { round, showsCurrent, nodes, loading };
}
