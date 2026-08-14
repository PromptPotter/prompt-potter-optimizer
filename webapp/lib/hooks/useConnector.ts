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
//      Render-phase guarded reset (`webapp/CLAUDE.md § State reset on
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
  type BackendHealthResponse,
  type BackendResponse,
  type NestedPipelineRef,
  type NodeConfigParam,
  type NodeOutputSchema,
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
  phase: null,
  nests: null,
};

// Connector reachability is rare-changing; a 5 s probe is plenty and stays
// efficient (matches the offline reconnect cadence elsewhere).
const HEALTH_INTERVAL_MS = 5000;

function useConnectorViewEngine(datasetName: string | null): ConnectorView {
  // Poll health only with a confirmed session; a 401 re-probes /auth/me so
  // the loop halts when the session dies instead of storming (useAuthGate).
  const { authed, onAuthError } = useAuthGate();
  const [backends, setBackends] = useState<BackendResponse[]>([]);
  const [view, setView] = useState<PipelineView | null>(null);
  // The dataset the resolved fields below were fetched FOR, plus whether that
  // fetch failed. Stamping the key (rather than resetting state in the effect)
  // is the pure-derivation recipe in webapp/CLAUDE.md § State reset on prop
  // change: freshness is computed during render, so switching campaigns can
  // never paint the previous dataset's pipeline for a frame.
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
  const [nests, setNests] = useState<NestedPipelineRef | null>(null);

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
    setNests(null);
  }

  // Drop the backend list when `authed` goes false (logout / dead
  // session) — render-phase guarded reset, so no stale list survives sign-out
  // (webapp/CLAUDE.md § State reset on prop change).
  const [prevAuthed, setPrevAuthed] = useState(authed);
  if (authed !== prevAuthed) {
    setPrevAuthed(authed);
    if (!authed) setBackends([]);
  }

  // Backends — one-shot once a session is confirmed; immutable within it.
  // Gated on `authed` so an anon preview never fires the protected `/backends`
  // read (it would 401; frontend-surface-contract.md § I5).
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

  // Dataset overlay — refetches on every datasetName change, from the committed
  // dataset on disk. A pre-commit check-in has no dataset dir; it renders its
  // pipeline straight off the draft wire via `StaticConnectorProvider` instead.
  useEffect(() => {
    if (!datasetName) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = (await fetchDatasetPipeline(datasetName)) as {
          view?: PipelineView | null;
          connector?: string | null;
          // `backend_type` is a top-level connector fact (peer of `connector`), NOT nested
          // in `pipeline` — the server's `pipeline` is the parsed `PipelineSchema`, which
          // drops it. Reading it here is what lets `isSelfOptimization` recognise pp-self.
          backend_type?: string | null;
          node_config_schema?: Record<string, NodeConfigParam[]> | null;
          node_output_schema?: Record<string, NodeOutputSchema | null> | null;
          nests?: NestedPipelineRef | null;
        };
        if (!cancelled) {
          setView(resp?.view ?? null);
          setConnector(resp?.connector ?? null);
          setBackendType(resp?.backend_type ?? null);
          setNodeConfigSchema(resp?.node_config_schema ?? null);
          setNodeOutputSchema(resp?.node_output_schema ?? null);
          setNests(resp?.nests ?? null);
          setLoaded({ key: datasetName, failed: false });
        }
      } catch {
        if (!cancelled) {
          setView(null);
          setConnector(null);
          setBackendType(null);
          setNodeConfigSchema(null);
          setNodeOutputSchema(null);
          setNests(null);
          setLoaded({ key: datasetName, failed: true });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetName]);

  const { dash, isLive } = useDashboard();
  const currentNodes = useMemo(
    () => (dash?.current_round.nodes as Record<string, NodeDataLike> | undefined) ?? {},
    [dash],
  );
  // Fine-grained activity phase — drives the hero's running/idle indicator.
  const phase = typeof dash?.state === "string" ? dash.state : null;

  // Resolved backend id (dataset's connector name → registered backend). Drives
  // the reachability probe below; null until both streams resolve.
  const activeId = useMemo(
    () => (connector ? backends.find((b) => b.name === connector)?.id ?? null : null),
    [connector, backends],
  );

  // Slow connector reachability probe. Server-side ping of the backend's own
  // GET /status; the connector node shows the result as a dot + footer line.
  // Separate from the 2 s dashboard poll — this is "is the dependency up", not
  // "is the optimizer scoring". Drop stale health when the backend changes.
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
    if (!datasetName) return { ...EMPTY, isLive, currentNodes, phase };
    // Every field below is dataset-derived, so until this dataset's own fetch has
    // landed they belong to the PREVIOUS one. Report "loading" and withhold them
    // rather than paint another campaign's pipeline as this one's.
    if (loaded?.key !== datasetName) {
      return { ...EMPTY, pipelineStatus: "loading", isLive, currentNodes, phase };
    }
    const pipelineStatus: PipelineStatus = loaded.failed ? "error" : "ok";
    const active = connector ? backends.find((b) => b.name === connector) ?? null : null;
    const baseUrl = active?.base_url ?? null;
    // One physical endpoint can hold many registrations — today the mint/ingest
    // path registers a BackendConnection PER DATASET, all cloning the same
    // termnorm URL — so the raw list renders a dataset name + N duplicate
    // "termnorm" rows. Collapse to one row per (backend_type, base_url); active
    // first so it owns its endpoint and never re-appears under "Other backends".
    // (Upstream fix: stop the per-dataset registration so this is a no-op.)
    const distinct: BackendResponse[] = [];
    const seenEndpoints = new Set<string>();
    for (const b of active ? [active, ...backends.filter((b) => b !== active)] : backends) {
      const key = `${b.backend_type}|${b.base_url}`;
      if (seenEndpoints.has(key)) continue;
      seenEndpoints.add(key);
      distinct.push(b);
    }
    return {
      connector,
      backendType,
      view,
      pipelineStatus,
      active,
      others: active ? distinct.filter((b) => b !== active) : distinct,
      baseUrl,
      isTls: baseUrl ? baseUrl.startsWith("https://") : null,
      currentNodes,
      isLive,
      health,
      nodeConfigSchema,
      nodeOutputSchema,
      phase,
      nests,
    };
  }, [
    datasetName,
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
    phase,
    nests,
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

// A connector context filled from a value the caller already holds — no fetch, no
// health poll. The ingest "ready" panel uses it to seed the pipeline `view` +
// node schemas straight off the draft wire (a pre-commit check-in has no committed
// dataset dir to fetch). `fields` overlays the empty connector shape, so the caller
// supplies only what it has (the pipeline view + the node schemas); the live-run
// streams (backends, health, currentNodes) stay empty — setup needs none of them.
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
