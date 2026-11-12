// Starts BELOW the dataset `useConnector` already reads; each named dataset's read serves its whole nest.

import { nestedPipelinesRead, unread, type NestedPipelines } from "@/lib/api";
import { useRead } from "./useRead";
import type { NestedPipelineRef } from "@/lib/api/types";

const EMPTY: NestedPipelines = { layers: [], unread: null };

export function useNestedPipelines(roots: NestedPipelineRef[], enabled: boolean): NestedPipelines {
  const read = useRead(enabled && roots.length > 0 ? nestedPipelinesRead(roots) : null);

  if (!enabled || roots.length === 0) return EMPTY;
  if (read.status === "ready") return read.data;
  // Publish the named level now: zero layers would make the caller's level innermost for a frame.
  return { layers: roots.map(unread), unread: null };
}
