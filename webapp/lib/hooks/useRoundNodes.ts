"use client";
// The single resolver for which round's node blocks the optimizer card shows. Plain equality
// against `current_round.round`, never "has it closed": escalation writes land after the flush.

import { useMemo } from "react";
import { useDashboard } from "./useDashboard";
import { useEffectiveRound } from "./useEffectiveRound";
import { useRoundAudit } from "./useRoundFile";
import { useWorkspace } from "@/lib/workspace";
import type { NodeBlock } from "@/lib/types";

const EMPTY: Record<string, NodeBlock> = {};

export interface RoundNodes {
  round: number | null;
  // Not `isLiveView`: this asks whether the live block holds this round's nodes.
  showsCurrent: boolean;
  nodes: Record<string, NodeBlock>;
  // An empty map and an unfinished fetch otherwise both read "this node never fired".
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
