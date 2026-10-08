"use client";
// Joins `/backends` and `/campaigns/{id}/pipeline?at=` into one `ConnectorView`. Mount `ConnectorProvider` ONCE above its consumers: one health poll per app.

import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from "react";
import {
  fetchBackendHealth,
  fetchBackends,
  fetchCampaignPipeline,
  type BackendResponse,
  type CampaignPipelineResponse,
} from "@/lib/api";
import { readyData, useRead, type ReadResult } from "@/lib/hooks/useRead";
import { useCycleStream } from "@/lib/poll";
import type { ConnectorView, PipelineStatus } from "@/lib/types";

const EMPTY: ConnectorView = {
  connector: null,
  backendType: null,
  selfOptimization: false,
  optimizer: null,
  optimizerKnobs: null,
  view: null,
  pipelineStatus: "unbound",
  active: null,
  others: [],
  baseUrl: null,
  isTls: null,
  isLive: false,
  health: null,
  nodeConfigSchema: null,
  nodeOutputSchema: null,
  modelCapabilities: {},
  reach: null,
  isSingleNode: false,
  nests: null,
};

const HEALTH_INTERVAL_MS = 5000;
const NO_BACKENDS: BackendResponse[] = [];

// The one named read of a campaign's resolved pipeline; a null or "" `at` is the campaign root.
export function useCampaignPipeline(
  campaignId: string | null,
  at: string | null,
): ReadResult<CampaignPipelineResponse> {
  return useRead(
    campaignId
      ? {
          key: `${campaignId}\x1f${at ?? ""}`,
          conditional: (signal, etag) => fetchCampaignPipeline(campaignId, at, signal, etag),
        }
      : null,
    { surface: "campaign-pipeline" },
  );
}

function useConnectorViewEngine(campaignId: string | null, at: string | null): ConnectorView {
  // Gated on the session: an anon preview must never fire the protected read (I5).
  const backendsRead = useRead(
    { key: "", fetch: (signal) => fetchBackends(signal) },
    { surface: "backends", auth: true },
  );
  const backends =
    backendsRead.status === "ready"
      ? backendsRead.data
      : backendsRead.status === "idle"
        ? NO_BACKENDS
        : (backendsRead.kept ?? NO_BACKENDS);

  // Every field is server-resolved; the browser joins nothing (I9). A failed revalidation keeps
  // the body it painted over, so `error` is a read with nothing to show.
  const read = useCampaignPipeline(campaignId, at);
  const resp = read.status === "ready" ? read.data : read.status === "idle" ? null : read.kept;
  const pipelineStatus: PipelineStatus =
    read.status === "idle"
      ? "unbound"
      : resp
        ? "ok"
        : read.status === "loading"
          ? "loading"
          : "error";

  const { isLive } = useCycleStream();

  const connector = resp?.connector ?? null;
  const activeId = useMemo(
    () => (connector ? backends.find((b) => b.name === connector)?.id ?? null : null),
    [connector, backends],
  );

  // Our own API down is the dashboard banner's to say: a failed read is unknown, not offline.
  const health = readyData(
    useRead(
      activeId ? { key: activeId, fetch: (signal) => fetchBackendHealth(activeId, signal) } : null,
      { surface: "backend-health", auth: true, intervalMs: HEALTH_INTERVAL_MS },
    ),
  );

  return useMemo<ConnectorView>(() => {
    if (!resp) {
      const others = pipelineStatus === "error" ? backends : [];
      return { ...EMPTY, pipelineStatus, others, isLive };
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
      pipelineStatus,
      active,
      others: active ? backends.filter((b) => b !== active) : backends,
      baseUrl,
      isTls: baseUrl ? baseUrl.startsWith("https://") : null,
      isLive,
      health,
      nodeConfigSchema: resp.node_config_schema,
      nodeOutputSchema: resp.node_output_schema,
      modelCapabilities: resp.model_capabilities,
      reach: resp.reach,
      isSingleNode: resp.is_single_node,
      nests: resp.nests,
    };
  }, [resp, pipelineStatus, backends, isLive, health]);
}

const ConnectorContext = createContext<ConnectorView | null>(null);

export function ConnectorProvider({
  campaignId,
  at,
  children,
}: {
  campaignId: string | null;
  // `parse_subject` grammar; null is the campaign root.
  at?: string | null;
  children: ReactNode;
}) {
  const view = useConnectorViewEngine(campaignId, at ?? null);
  return createElement(ConnectorContext.Provider, { value: view }, children);
}

// A second TRANSPORT of the same served resolution (`resolve_pipeline_for_draft`), never a
// second source: overlay anything but a draft response's fields here and the stores diverge.
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
