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
import { useCheckinDraft } from "@/lib/state/ingest-flow";
import { heldRead, type ReadResult } from "@/lib/read-cache";
import { useWorkspace } from "@/lib/workspace";
import type { NodeScope } from "@/lib/address";
import { nodeSchemaReading } from "@/lib/derivations";
import type { NodeSchemaReading } from "@/lib/types";

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
  const { viewed } = useWorkspace();
  return useMemo(
    () =>
      viewed
        ? subjectKey("course", [viewed.leaf.campaignId, viewed.leaf.cycleId], viewed.path.slice(0, -1))
        : null,
    [viewed],
  );
}

export function useConnector(): ReadResult<CampaignPipelineResponse> {
  const { viewed } = useWorkspace();
  const draft = useCheckinDraft();
  const served = useCampaignPipeline(draft ? null : (viewed?.leaf.campaignId ?? null), useLeafSubject());
  // A viewed check-in's draft (`resolve_pipeline_for_draft`) is a second transport of the same answer, never a second source.
  return useMemo(
    () => (draft ? heldRead(draft.pipeline) : served),
    [draft, served],
  );
}

// Which pipeline a node surface reads: a picked point's campaign at its subject; null, the viewed one.
export interface PipelineAt {
  campaignId: string;
  subject: string;
}

// The node rows of the pipeline a surface names, for one scope: the one door every node surface
// reads, so a host hands identity, never the rows.
export function useNodeSchema(at: PipelineAt | null, scope: NodeScope): NodeSchemaReading {
  const viewed = useConnector();
  const picked = useCampaignPipeline(at?.campaignId ?? null, at?.subject ?? null);
  return nodeSchemaReading(at ? picked : viewed, scope);
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
