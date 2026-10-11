"use client";

import { useMemo } from "react";
import { roundAuditRead } from "@/lib/api";
import { useCycleStream } from "@/lib/poll";
import { useObserveSubject } from "./useObserveSubject";
import { readyData, useRead } from "./useRead";
import { useWorkspace } from "@/lib/workspace";
import type { NodeBlock } from "@/lib/api/types";

const EMPTY: Record<string, NodeBlock> = {};

export interface RoundNodes {
  round: number | null;
  showsCurrent: boolean;
  nodes: Record<string, NodeBlock>;
  // An empty map and an unfinished fetch otherwise both read "this node never fired".
  loading: boolean;
}

export function useRoundNodes(): RoundNodes {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const { round } = useObserveSubject();
  const showsCurrent = round != null && round === (dash?.current_round.round ?? null);
  const audit = useRead(
    viewedPath && round != null && !showsCurrent ? roundAuditRead(viewedPath, round) : null,
  );
  const doc = readyData(audit);
  const loading = audit.status === "loading";
  const nodes = useMemo(() => {
    if (round == null) return EMPTY;
    if (showsCurrent) return dash?.current_round.nodes ?? EMPTY;
    return doc?.nodes ?? EMPTY;
  }, [round, showsCurrent, dash, doc]);
  return { round, showsCurrent, nodes, loading };
}
