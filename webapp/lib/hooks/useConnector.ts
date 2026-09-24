"use client";
// Joins `/backends`, `/campaigns/{id}/pipeline?at=` and the live `dash` nodes into one
// `ConnectorView`. Mount `ConnectorProvider` ONCE above its consumers: one health poll per app.

import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  fetchBackendHealth,
  fetchBackends,
  fetchCampaignPipeline,
  type BackendHealthResponse,
  type BackendResponse,
  type ModelCapability,
  type NestedPipelineRef,
  type NodeConfigParam,
  type NodeOutputSchema,
  type NodeReach,
} from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { usePoll } from "@/lib/hooks/usePoll";
import type { ConnectorView, PipelineStatus } from "@/lib/types";
import type { NodeDataLike, PipelineView } from "@/components/workflow";

const EMPTY: ConnectorView = {
  connector: null,
  backendType: null,
  view: null,
  pipelineStatus: "unbound",
  active: null,
  others: [],
  baseUrl: null,
  isTls: null,
  currentNodes: {},
  isLive: false,
  health: null,
  nodeConfigSchema: null,
  nodeOutputSchema: null,
  modelCapabilities: {},
  reach: null,
  isSingleNode: false,
  phase: null,
  nests: null,
};

const HEALTH_INTERVAL_MS = 5000;

// Minted HERE only, so the reset, the freshness test and the fetch stamp cannot disagree.
function connectorKey(campaignId: string, at: string | null): string {
  return `${campaignId}|${at ?? ""}`;
}

function useConnectorViewEngine(campaignId: string | null, at: string | null): ConnectorView {
  const key = campaignId ? connectorKey(campaignId, at) : null;
  const { authed, onAuthError } = useAuthGate();
  const [backends, setBackends] = useState<BackendResponse[]>([]);
  const [view, setView] = useState<PipelineView | null>(null);
  const [loaded, setLoaded] = useState<{ key: string; failed: boolean } | null>(null);
  const [connector, setConnector] = useState<string | null>(null);
  const [backendType, setBackendType] = useState<string | null>(null);
  const [nodeConfigSchema, setNodeConfigSchema] = useState<Record<
    string,
    NodeConfigParam[]
  > | null>(null);
  const [nodeOutputSchema, setNodeOutputSchema] = useState<Record<
    string,
    NodeOutputSchema | null
  > | null>(null);
  // `{}`, not `null`: unresolved and empty both read UNKNOWN — render nothing struck.
  const [modelCapabilities, setModelCapabilities] = useState<Record<string, ModelCapability>>({});
  const [reach, setReach] = useState<Record<string, NodeReach> | null>(null);
  const [isSingleNode, setIsSingleNode] = useState(false);
  const [nests, setNests] = useState<NestedPipelineRef | null>(null);

  const [prevKey, setPrevKey] = useState(key);
  if (key !== prevKey) {
    setPrevKey(key);
    setView(null);
    setConnector(null);
    setBackendType(null);
    setNodeConfigSchema(null);
    setNodeOutputSchema(null);
    setModelCapabilities({});
    setReach(null);
    setIsSingleNode(false);
    setNests(null);
  }

  const [prevAuthed, setPrevAuthed] = useState(authed);
  if (authed !== prevAuthed) {
    setPrevAuthed(authed);
    if (!authed) setBackends([]);
  }

  // Gated on `authed`: an anon preview must never fire the protected read (I5).
  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchBackends();
        if (!cancelled) setBackends(list);
      } catch (e) {
        if (!cancelled) {
          onAuthError(e);
          setBackends([]);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [authed, onAuthError]);

  // Every field is server-resolved; the browser joins nothing (I9).
  useEffect(() => {
    if (!campaignId) return;
    let cancelled = false;
    const stamp = connectorKey(campaignId, at);
    (async () => {
      try {
        // `backend_type` is top-level, never in `view`: the parsed `PipelineSchema` drops it.
        const resp = await fetchCampaignPipeline(campaignId, at);
        if (!cancelled) {
          setView((resp?.view ?? null) as PipelineView | null);
          setConnector(resp?.connector ?? null);
          setBackendType(resp?.backend_type ?? null);
          setNodeConfigSchema(resp?.node_config_schema ?? null);
          setNodeOutputSchema(
            (resp?.node_output_schema ?? null) as Record<string, NodeOutputSchema | null> | null,
          );
          setModelCapabilities(
            (resp?.model_capabilities ?? {}) as Record<string, ModelCapability>,
          );
          setReach(resp?.reach ?? null);
          setIsSingleNode(!!resp?.is_single_node);
          setNests(resp?.nests ?? null);
          setLoaded({ key: stamp, failed: false });
        }
      } catch {
        if (!cancelled) {
          setView(null);
          setConnector(null);
          setBackendType(null);
          setNodeConfigSchema(null);
          setNodeOutputSchema(null);
          setModelCapabilities({});
          setReach(null);
          setIsSingleNode(false);
          setNests(null);
          setLoaded({ key: stamp, failed: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, at]);

  const { dash, isLive } = useDashboard();
  const currentNodes = useMemo(
    () => (dash?.current_round.nodes as Record<string, NodeDataLike> | undefined) ?? {},
    [dash],
  );
  const phase = typeof dash?.state === "string" ? dash.state : null;

  const activeId = useMemo(
    () => (connector ? backends.find((b) => b.name === connector)?.id ?? null : null),
    [connector, backends],
  );

  const [health, setHealth] = useState<BackendHealthResponse | null>(null);
  const [prevActiveId, setPrevActiveId] = useState(activeId);
  if (activeId !== prevActiveId) {
    setPrevActiveId(activeId);
    setHealth(null);
  }
  const healthTick = useCallback(
    async (signal: AbortSignal) => {
      if (!activeId) return;
      try {
        const h = await fetchBackendHealth(activeId, signal);
        if (!signal.aborted) setHealth(h);
      } catch (e) {
        // Our own API down is the dashboard banner's to say: health stays unknown, not offline.
        if (!signal.aborted) {
          onAuthError(e);
          setHealth(null);
        }
      }
    },
    [activeId, onAuthError],
  );
  usePoll(healthTick, { intervalMs: HEALTH_INTERVAL_MS, enabled: !!activeId && authed });

  return useMemo<ConnectorView>(() => {
    if (!key) return { ...EMPTY, isLive, currentNodes, phase };
    // Until this key's fetch lands, every field below belongs to the PREVIOUS campaign.
    if (loaded?.key !== key) {
      return { ...EMPTY, pipelineStatus: "loading", isLive, currentNodes, phase };
    }
    const pipelineStatus: PipelineStatus = loaded.failed ? "error" : "ok";
    const active = connector ? backends.find((b) => b.name === connector) ?? null : null;
    const baseUrl = active?.base_url ?? null;
    return {
      connector,
      backendType,
      view,
      pipelineStatus,
      active,
      others: active ? backends.filter((b) => b !== active) : backends,
      baseUrl,
      isTls: baseUrl ? baseUrl.startsWith("https://") : null,
      currentNodes,
      isLive,
      health,
      nodeConfigSchema,
      nodeOutputSchema,
      modelCapabilities,
      reach,
      isSingleNode,
      phase,
      nests,
    };
  }, [
    key,
    connector,
    backendType,
    view,
    loaded,
    backends,
    currentNodes,
    isLive,
    health,
    nodeConfigSchema,
    nodeOutputSchema,
    modelCapabilities,
    reach,
    isSingleNode,
    phase,
    nests,
  ]);
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
