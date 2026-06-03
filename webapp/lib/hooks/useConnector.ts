"use client";
// Joins the three connector-state streams into one typed `ConnectorView`,
// exposed via a single shared `ConnectorProvider` so every consumer
// (`ChatPane`, `ScoringInspector`, `SteerForkPanel`) rides ONE set of
// fetches and ONE 5 s `/status` health poll. Calling the engine hook per
// component — the prior shape — fanned the `/backends` + `/pipeline` reads
// and the health poll out N times, flooding the backend's `/status`. Read
// it with `useConnector()`; mount `ConnectorProvider` once above the
// consumers (see `AppShellInner`).
//
// Three input streams, all orthogonal (no stitching — they describe
// different facts, not the same fact at different freshness levels):
//
//   1. `/backends` — operator-level registered backends. One-shot on
//      mount. Immutable within a session.
//   2. `/datasets/{name}/pipeline` — dataset-overlay pipeline + the
//      connector name and backend_type. One-shot per `datasetName`.
//      Render-phase guarded reset (`webapp/AGENTS.md § State reset on
//      prop change`) clears all dataset-keyed slots atomically so a unit
//      switch never shows a stale frame.
//   3. `useDashboard()::dash.current_round.nodes` + `isLive` — live
//      per-LLM-node observations from `dashboard.json` (polled every 2 s
//      by `useCycleStream`).
//
// Match dataset's `backend_name` to a registered backend by `.name` to
// resolve `base_url` + the rest of `BackendConnection`. Case-sensitive,
// matching the wire field. When M12 adds an authoritative endpoint, the
// match moves to the server; this hook becomes a thin wire wrapper.

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
  fetchDatasetPipeline,
  type BackendHealth,
  type BackendInfo,
  type NodeConfigParam,
  type NodeOutputSchema,
} from "@/lib/api";
import { useAuthGate } from "@/lib/auth-context";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { usePoll } from "@/lib/hooks/usePoll";
import type { ConnectorView } from "@/lib/types/connector";
import type { NodeDataLike, PipelineView } from "@/components/workflow/types";

const EMPTY: ConnectorView = {
  connector: null,
  backendType: null,
  view: null,
  active: null,
  others: [],
  baseUrl: null,
  isTls: null,
  currentNodes: {},
  isLive: false,
  health: null,
  nodeConfigSchema: null,
  nodeOutputSchema: null,
  startingPrompt: null,
};

// Connector reachability is rare-changing; a 5 s probe is plenty and stays
// efficient (matches the offline reconnect cadence elsewhere).
const HEALTH_INTERVAL_MS = 5000;

function useConnectorViewEngine(datasetName: string | null): ConnectorView {
  // Poll health only with a confirmed session; a 401 re-probes /auth/me so
  // the loop halts when the session dies instead of storming (useAuthGate).
  const { authed, onAuthError } = useAuthGate();
  const [backends, setBackends] = useState<BackendInfo[]>([]);
  const [view, setView] = useState<PipelineView | null>(null);
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
  const [startingPrompt, setStartingPrompt] = useState<Record<string, unknown> | null>(null);

  // Render-phase guarded reset — drops every dataset-keyed slot together
  // the same render the dataset id changes, so no consumer ever sees a
  // half-swapped frame mixing the prior dataset's view with the new
  // dataset's connector.
  const [prevDatasetName, setPrevDatasetName] = useState(datasetName);
  if (datasetName !== prevDatasetName) {
    setPrevDatasetName(datasetName);
    setView(null);
    setConnector(null);
    setBackendType(null);
    setNodeConfigSchema(null);
    setNodeOutputSchema(null);
    setStartingPrompt(null);
  }

  // Backends — one-shot on mount; immutable within a session.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchBackends();
        if (!cancelled) setBackends(list);
      } catch {
        if (!cancelled) setBackends([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Dataset overlay — refetches on every datasetName change.
  useEffect(() => {
    if (!datasetName) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = (await fetchDatasetPipeline(datasetName)) as {
          view?: PipelineView | null;
          connector?: string | null;
          pipeline?: { backend_type?: string | null } | null;
          node_config_schema?: Record<string, NodeConfigParam[]> | null;
          node_output_schema?: Record<string, NodeOutputSchema | null> | null;
          starting_prompt?: Record<string, unknown> | null;
        };
        if (!cancelled) {
          setView(resp?.view ?? null);
          setConnector(resp?.connector ?? null);
          setBackendType(resp?.pipeline?.backend_type ?? null);
          setNodeConfigSchema(resp?.node_config_schema ?? null);
          setNodeOutputSchema(resp?.node_output_schema ?? null);
          setStartingPrompt(resp?.starting_prompt ?? null);
        }
      } catch {
        if (!cancelled) {
          setView(null);
          setConnector(null);
          setBackendType(null);
          setNodeConfigSchema(null);
          setNodeOutputSchema(null);
          setStartingPrompt(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetName]);

  const { dash, isLive } = useDashboard();
  const currentNodes = useMemo(
    () => (dash?.current_round?.nodes as Record<string, NodeDataLike> | undefined) ?? {},
    [dash],
  );

  // Resolved backend id (dataset's connector name → registered backend). Drives
  // the reachability probe below; null until both streams resolve.
  const activeId = useMemo(
    () => (connector ? backends.find((b) => b.name === connector)?.id ?? null : null),
    [connector, backends],
  );

  // Slow connector reachability probe. Server-side ping of the backend's own
  // GET /status; the connector node shows the result as a dot + footer line.
  // Separate from the 2 s dashboard poll — this is "is the dependency up", not
  // "is the optimizer scoring". Drop stale health the render the backend changes.
  const [health, setHealth] = useState<BackendHealth | null>(null);
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
        // PromptPotter API itself unreachable — that's the dashboard banner's
        // job; leave connector health unknown rather than asserting offline.
        // A 401 still flips the auth gate so the loop stops.
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
    if (!datasetName) return { ...EMPTY, isLive, currentNodes };
    const active = connector ? backends.find((b) => b.name === connector) ?? null : null;
    const baseUrl = active?.base_url ?? null;
    return {
      connector,
      backendType,
      view,
      active,
      others: active ? backends.filter((b) => b !== active) : backends,
      baseUrl,
      isTls: baseUrl ? baseUrl.startsWith("https://") : null,
      currentNodes,
      isLive,
      health,
      nodeConfigSchema,
      nodeOutputSchema,
      startingPrompt,
    };
  }, [
    datasetName,
    connector,
    backendType,
    view,
    backends,
    currentNodes,
    isLive,
    health,
    nodeConfigSchema,
    nodeOutputSchema,
    startingPrompt,
  ]);
}

// Shared connector state. Mounted once (per viewed dataset) above all
// consumers; `useConnector()` reads it. This is what collapses the former
// per-component fan-out into a single health poll + single overlay fetch.
const ConnectorContext = createContext<ConnectorView | null>(null);

export function ConnectorProvider({
  datasetName,
  children,
}: {
  datasetName: string | null;
  children: ReactNode;
}) {
  const view = useConnectorViewEngine(datasetName);
  return createElement(ConnectorContext.Provider, { value: view }, children);
}

export function useConnector(): ConnectorView {
  const v = useContext(ConnectorContext);
  if (!v) throw new Error("useConnector must be used inside <ConnectorProvider>");
  return v;
}
