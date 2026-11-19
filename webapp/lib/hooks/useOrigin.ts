// served: the cycle's `facts.origin`; null until the subtree places it.

import { useMemo } from "react";
import { originAt, originPoint, type ObservePoint } from "@/lib/derivations";
import { useViewedLineage } from "@/lib/state/lineage";
import { useWorkspace } from "@/lib/state/workspace";

export function useOrigin(): ObservePoint | null {
  const { viewed } = useWorkspace();
  const viewedPath = viewed?.path ?? null;
  const { index } = useViewedLineage();
  return useMemo(() => originPoint(originAt(index, viewedPath)), [index, viewedPath]);
}
