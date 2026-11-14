import { useMemo } from "react";
import type { ObserveConfig, ObservePoint } from "@/lib/derivations";
import { useRound } from "@/lib/hooks/useRound";
import type { RoundView } from "@/lib/api/types";
import type { ReadResult } from "@/lib/read-cache";
import { useWorkspace } from "@/lib/state/workspace";

export interface SearchpointRead {
  cfg: ObserveConfig | null;
  // The round file's read; idle while the round is unfiled and `cfg` is the dashboard's.
  read: ReadResult<RoundView>;
  unfiled: boolean;
}

export function useSearchpointRead(
  point: ObservePoint | null,
  nodeId?: string | null,
): SearchpointRead {
  // From the workspace, NOT the viewed dashboard, which nulls on a unit switch and would starve the round read.
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const { cfg: cfgOf, read, unfiled } = useRound(viewedPath, point?.round ?? null);
  const key = point?.key ?? null;
  const title = point?.title ?? "";
  const cfg = useMemo(() => cfgOf(key, title, nodeId), [cfgOf, key, title, nodeId]);
  return { cfg, read, unfiled };
}
