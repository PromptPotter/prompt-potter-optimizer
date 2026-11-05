// Mount `ConnectorProvider` ONCE above its consumers: one health poll per app.

import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import { backendHealthRead, backendsRead, campaignPipelineRead } from "@/lib/api";
import type { BackendResponse, CampaignPipelineResponse, DraftCampaignWire } from "@/lib/api/types";
import { pipelineReadStatus } from "@/lib/derivations";
import { readyData, shownData, useRead } from "@/lib/hooks/useRead";
import type { ReadResult } from "@/lib/read-cache";
import type { ConnectorView } from "@/lib/types";

const EMPTY: ConnectorView = {
  connector: null,
  backendType: null,
  selfOptimization: false,
  optimizer: null,
  optimizerSetup: null,
  view: null,
  schema: { status: "unbound", config: null, output: null, isSingleNode: false },
  active: null,
  others: [],
  baseUrl: null,
  isTls: null,
  health: null,
  modelCapabilities: {},
  reach: null,
  nests: [],
  cellTerms: [],
};

const NO_BACKENDS: BackendResponse[] = [];

export function useCampaignPipeline(
  campaignId: string | null,
  at: string | null,
): ReadResult<CampaignPipelineResponse> {
  return useRead(campaignId ? campaignPipelineRead(campaignId, at) : null);
}

function useConnectorViewEngine(campaignId: string | null, at: string | null): ConnectorView {
  // Gated on the session: an anon preview must never fire the protected read (I5).
  const backends = shownData(useRead(backendsRead())) ?? NO_BACKENDS;

  const read = useCampaignPipeline(campaignId, at);
  const resp = shownData(read);
  const pipelineStatus = pipelineReadStatus(read);

  const backendType = resp?.backend_type ?? null;
  const activeId = useMemo(
    () => (backendType ? backends.find((b) => b.id === backendType)?.id ?? null : null),
    [backendType, backends],
  );

  // Our own API down is the dashboard banner's to say: a failed read is unknown, not offline.
  const health = readyData(
    useRead(activeId ? backendHealthRead(activeId) : null),
  );

  return useMemo<ConnectorView>(() => {
    if (!resp) {
      const others = typeof pipelineStatus === "object" ? backends : [];
      return { ...EMPTY, schema: { ...EMPTY.schema, status: pipelineStatus }, others };
    }
    const active = backends.find((b) => b.id === resp.backend_type) ?? null;
    const baseUrl = active?.base_url ?? null;
    return {
      connector: resp.connector,
      // Top-level, never in `view`: the parsed `PipelineSchema` drops it.
      backendType: resp.backend_type,
      selfOptimization: resp.self_optimization,
      optimizer: resp.optimizer,
      optimizerSetup: resp.optimizer_setup,
      view: resp.view,
      schema: {
        status: pipelineStatus,
        config: resp.node_config_schema,
        output: resp.node_output_schema,
        isSingleNode: resp.is_single_node,
      },
      active,
      others: active ? backends.filter((b) => b !== active) : backends,
      baseUrl,
      isTls: baseUrl ? baseUrl.startsWith("https://") : null,
      health,
      modelCapabilities: resp.model_capabilities,
      reach: resp.reach,
      nests: resp.nests,
      cellTerms: resp.cell_terms,
    };
  }, [resp, pipelineStatus, backends, health]);
}

const ConnectorContext = createContext<ConnectorView | null>(null);

// A viewed check-in's draft (`resolve_pipeline_for_draft`) is a second transport of the same answer, never a second source.
function draftConnector(draft: DraftCampaignWire): ConnectorView {
  return {
    ...EMPTY,
    connector: draft.connector,
    optimizer: draft.optimization_overrides.optimizer,
    optimizerSetup: draft.optimizer_setup,
    view: draft.pipeline_view,
    // Landed by construction; the default (`unbound`) would read as no campaign.
    schema: {
      status: "ok",
      config: draft.node_config_schema,
      output: draft.node_output_schema,
      isSingleNode: draft.is_single_node,
    },
    modelCapabilities: draft.model_capabilities,
    reach: draft.reach,
  };
}

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
  const served = useConnectorViewEngine(campaignId, at);
  const drafted = useMemo(() => (draft ? draftConnector(draft) : null), [draft]);
  return createElement(ConnectorContext.Provider, { value: drafted ?? served }, children);
}

export function useConnector(): ConnectorView {
  const v = useContext(ConnectorContext);
  if (!v) throw new Error("useConnector must be used inside <ConnectorProvider>");
  return v;
}
