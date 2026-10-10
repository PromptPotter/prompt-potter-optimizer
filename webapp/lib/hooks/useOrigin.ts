"use client";
// served: the tree's `origin_row`; null until the tree places it.

import { useMemo } from "react";
import { originAt, originPoint, type ObservePoint } from "@/lib/derivations";
import { useViewedLineage } from "@/lib/lineage";
import { useCycleStream } from "@/lib/poll";
import { useWorkspace } from "@/lib/workspace";

export function useOrigin(): ObservePoint | null {
  const { dash } = useCycleStream();
  const { viewedPath } = useWorkspace();
  const { index } = useViewedLineage();
  return useMemo(() => originPoint(dash, originAt(index, viewedPath)), [dash, index, viewedPath]);
}
