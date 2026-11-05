// Mount `ConnectorProvider` ONCE above its consumers: one health poll per app.

import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import {
  backendHealthRead,
  backendsRead,
  campaignPipelineRead,
  type BackendHealthResponse,
  type BackendResponse,
  type CampaignPipelineResponse,
  type DraftCampaignWire,
} from "@/lib/api";
import { CONNECTOR_HEALTH_POLL_MS } from "@/lib/cadence";
import { readyData, shownData, useRead, type ReadResult } from "@/lib/hooks/useRead";

// The registered backends beside the one the viewed pipeline runs on.
export interface BackendReading {
  active: BackendResponse | null;
  others: BackendResponse[];
  // Real reachability, distinct from the stream's `isLive` (is the optimizer scoring through it now).
  health: BackendHealthResponse | null;
}

interface ConnectorValue {
  pipeline: ReadResult<CampaignPipelineResponse>;
  backend: BackendReading;
}

const NO_BACKENDS: BackendResponse[] = [];

export function useCampaignPipeline(
  campaignId: string | null,
  at: string | null,
): ReadResult<CampaignPipelineResponse> {
  return useRead(campaignId ? campaignPipelineRead(campaignId, at) : null);
}

function useBackendReading(backendType: string | null): BackendReading {
  // Gated on the session: an anon preview must never fire the protected read (I5).
  const backends = shownData(useRead(backendsRead(), { auth: true })) ?? NO_BACKENDS;
  const active = useMemo(
    () => (backendType ? (backends.find((b) => b.id === backendType) ?? null) : null),
    [backendType, backends],
  );
  // Our own API down is the dashboard banner's to say: a failed read is unknown, not offline.
  const health = readyData(
    useRead(active ? backendHealthRead(active.id) : null, {
      auth: true,
      intervalMs: CONNECTOR_HEALTH_POLL_MS,
    }),
  );
  return useMemo(
    () => ({ active, others: active ? backends.filter((b) => b !== active) : backends, health }),
    [active, backends, health],
  );
}

const ConnectorContext = createContext<ConnectorValue | null>(null);

export function ConnectorProvider({
  campaignId,
  at,
  draft,
  children,
}: {
  campaignId: string | null;
  at: string | null;
  draft: DraftCampaignWire | null;
  children: ReactNode;
}) {
  const served = useCampaignPipeline(draft ? null : campaignId, at);
  // A viewed check-in's draft (`resolve_pipeline_for_draft`) is a second transport of the same answer, never a second source.
  const pipeline = useMemo<ReadResult<CampaignPipelineResponse>>(
    () => (draft ? { status: "ready", data: draft.pipeline } : served),
    [draft, served],
  );
  const backend = useBackendReading(shownData(pipeline)?.backend_type ?? null);
  const value = useMemo(() => ({ pipeline, backend }), [pipeline, backend]);
  return createElement(ConnectorContext.Provider, { value }, children);
}

function useConnectorValue(): ConnectorValue {
  const v = useContext(ConnectorContext);
  if (!v) throw new Error("useConnector must be used inside <ConnectorProvider>");
  return v;
}

export function useConnector(): ReadResult<CampaignPipelineResponse> {
  return useConnectorValue().pipeline;
}

export function useBackend(): BackendReading {
  return useConnectorValue().backend;
}
