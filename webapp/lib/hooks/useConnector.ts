// The viewed pipeline: each caller its own `useRead` subscription, the cache's one flight per id.

import { useMemo } from "react";
import { backendHealthRead, backendsRead, campaignPipelineRead, subjectKey } from "@/lib/api";
import type {
  BackendHealthResponse,
  BackendResponse,
  CampaignPipelineResponse,
  CellTerm,
} from "@/lib/api/types";
import { readyData, shownData, useRead } from "@/lib/hooks/useRead";
import { pathLeaf } from "@/lib/ids";
import { useCheckinDraft } from "@/lib/ingest-flow";
import type { ReadResult } from "@/lib/read-cache";
import { useWorkspace } from "@/lib/workspace";

// The registered backends beside the one the viewed pipeline runs on.
export interface BackendReading {
  active: BackendResponse | null;
  others: BackendResponse[];
  // Real reachability, distinct from the stream's `isLive` (is the optimizer scoring through it now).
  health: BackendHealthResponse | null;
}

const NO_BACKENDS: BackendResponse[] = [];
const NO_TERMS: readonly CellTerm[] = [];

export function useCampaignPipeline(
  campaignId: string | null,
  at: string | null,
): ReadResult<CampaignPipelineResponse> {
  return useRead(campaignId ? campaignPipelineRead(campaignId, at) : null);
}

// The viewed leaf as a course subject: the hops ABOVE it are its chain, so an L4 inner run
// resolves its OWN pipeline.
export function useLeafSubject(): string | null {
  const { viewedPath } = useWorkspace();
  return useMemo(() => {
    const leaf = viewedPath ? pathLeaf(viewedPath) : null;
    return leaf && viewedPath
      ? subjectKey("course", [leaf.campaignId, leaf.cycleId], viewedPath.slice(0, -1))
      : null;
  }, [viewedPath]);
}

export function useConnector(): ReadResult<CampaignPipelineResponse> {
  const { leafCampaignId } = useWorkspace();
  const draft = useCheckinDraft();
  const served = useCampaignPipeline(draft ? null : leafCampaignId, useLeafSubject());
  // A viewed check-in's draft (`resolve_pipeline_for_draft`) is a second transport of the same answer, never a second source.
  return useMemo(
    () => (draft ? { status: "ready", data: draft.pipeline } : served),
    [draft, served],
  );
}

export function useBackend(): BackendReading {
  const connector = shownData(useConnector())?.connector ?? null;
  // Gated on the session: an anon preview must never fire the protected read (I5).
  const backends = shownData(useRead(backendsRead())) ?? NO_BACKENDS;
  const active = useMemo(
    () => (connector ? (backends.find((b) => b.id === connector) ?? null) : null),
    [connector, backends],
  );
  // Our own API down is the dashboard banner's to say: a failed read is unknown, not offline.
  const health = readyData(useRead(active ? backendHealthRead(active.id) : null));
  return useMemo(
    () => ({ active, others: active ? backends.filter((b) => b !== active) : backends, health }),
    [active, backends, health],
  );
}

// served: `CampaignPipelineResponse.cell_terms`, the terms the connector adds to the core's;
// empty until the pipeline lands.
export function useCellTerms(): readonly CellTerm[] {
  return shownData(useConnector())?.cell_terms ?? NO_TERMS;
}
