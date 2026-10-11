"use client";
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
  type BackendResponse,
  type CampaignPipelineResponse,
} from "@/lib/api";
import { readyData, shownData, useRead, type ReadResult } from "@/lib/hooks/useRead";
import type { ConnectorView, PipelineStatus } from "@/lib/types";

const EMPTY: ConnectorView = {
  connector: null,
  backendType: null,
  selfOptimization: false,
  optimizer: null,
  optimizerKnobs: null,
  view: null,
  schema: { status: "unbound", config: null, output: null, isSingleNode: false },
  active: null,
  others: [],
  baseUrl: null,
  isTls: null,
  health: null,
  modelCapabilities: {},
  reach: null,
  nests: null,
};

const HEALTH_INTERVAL_MS = 5000;
const NO_BACKENDS: BackendResponse[] = [];

export function useCampaignPipeline(
  campaignId: string | null,
  at: string | null,
): ReadResult<CampaignPipelineResponse> {
  return useRead(campaignId ? campaignPipelineRead(campaignId, at) : null);
}

function useConnectorViewEngine(campaignId: string | null, at: string | null): ConnectorView {
  // Gated on the session: an anon preview must never fire the protected read (I5).
  const backends = shownData(useRead(backendsRead(), { auth: true })) ?? NO_BACKENDS;

  // A failed revalidation keeps the body it painted over, so `error` is a read with nothing to show.
  const read = useCampaignPipeline(campaignId, at);
  const resp = shownData(read);
  const pipelineStatus: PipelineStatus =
    read.status === "idle"
      ? "unbound"
      : resp
        ? "ok"
        : read.status === "loading"
          ? "loading"
          : "error";

  const connector = resp?.connector ?? null;
  const activeId = useMemo(
    () => (connector ? backends.find((b) => b.name === connector)?.id ?? null : null),
    [connector, backends],
  );

  // Our own API down is the dashboard banner's to say: a failed read is unknown, not offline.
  const health = readyData(
    useRead(activeId ? backendHealthRead(activeId) : null, {
      auth: true,
      intervalMs: HEALTH_INTERVAL_MS,
    }),
  );

  return useMemo<ConnectorView>(() => {
    if (!resp) {
      const others = pipelineStatus === "error" ? backends : [];
      return { ...EMPTY, schema: { ...EMPTY.schema, status: pipelineStatus }, others };
    }
    const active = resp.connector ? backends.find((b) => b.name === resp.connector) ?? null : null;
    const baseUrl = active?.base_url ?? null;
    return {
      connector: resp.connector,
      // Top-level, never in `view`: the parsed `PipelineSchema` drops it.
      backendType: resp.backend_type,
      selfOptimization: resp.self_optimization,
      optimizer: resp.optimizer,
      optimizerKnobs: resp.optimizer_knobs,
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
    };
  }, [resp, pipelineStatus, backends, health]);
}

const ConnectorContext = createContext<ConnectorView | null>(null);

export function ConnectorProvider({
  campaignId,
  at,
  children,
}: {
  campaignId: string | null;
  at?: string | null;
  children: ReactNode;
}) {
  const view = useConnectorViewEngine(campaignId, at ?? null);
  return createElement(ConnectorContext.Provider, { value: view }, children);
}

// `fields` is a draft response's (`resolve_pipeline_for_draft`) and nothing else: a second transport, never a second source.
export function StaticConnectorProvider({
  fields,
  children,
}: {
  fields: Partial<ConnectorView>;
  children: ReactNode;
}) {
  const value = useMemo<ConnectorView>(() => ({ ...EMPTY, ...fields }), [fields]);
  return createElement(ConnectorContext.Provider, { value }, children);
}

export function useConnector(): ConnectorView {
  const v = useContext(ConnectorContext);
  if (!v) throw new Error("useConnector must be used inside <ConnectorProvider>");
  return v;
}
