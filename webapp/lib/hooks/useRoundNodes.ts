import { useMemo } from "react";
import { roundAuditRead } from "@/lib/api";
import { useViewedDashboard } from "@/lib/poll";
import { useObserveSubject } from "./useObserveSubject";
import { readyData, useRead } from "./useRead";
import { useWorkspace } from "@/lib/workspace";
import type { NodeBlock, RoundAudit } from "@/lib/api/types";
import type { ReadResult } from "@/lib/read-cache";

const EMPTY: Record<string, NodeBlock> = {};

interface RoundNodes {
  round: number | null;
  showsCurrent: boolean;
  nodes: Record<string, NodeBlock>;
  // An empty map and an unfinished fetch otherwise both read "this node never fired"; idle while
  // the round shown is the dashboard's own.
  audit: ReadResult<RoundAudit | null>;
}

export function useRoundNodes(): RoundNodes {
  const { dash } = useViewedDashboard();
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const { round } = useObserveSubject();
  const showsCurrent = round != null && round === (dash?.current_round.round ?? null);
  const audit = useRead(
    viewedPath && round != null && !showsCurrent ? roundAuditRead(viewedPath, round) : null,
  );
  const doc = readyData(audit);
  const nodes = useMemo(() => {
    if (round == null) return EMPTY;
    if (showsCurrent) return dash?.current_round.nodes ?? EMPTY;
    return doc?.nodes ?? EMPTY;
  }, [round, showsCurrent, dash, doc]);
  return { round, showsCurrent, nodes, audit };
}
