"use client";
// served: the course's `facts.origin`; null until the subtree places it.

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
