"use client";
// Single-call hook that joins the three connector-state streams into one
// typed `ConnectorView`. Replaces the prior fan-out where DashboardPane
// owned four useStates + two useEffects + a render-phase reset, and
// ChatPane plus TargetPipelineHero each carried four pass-through props
// for the same data.
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

import { useEffect, useMemo, useState } from "react";
import {
  fetchBackends,
  fetchDatasetPipeline,
  type BackendInfo,
  type OptimizerLocks,
} from "@/lib/api";
import { useDashboard } from "@/lib/hooks/useDashboard";
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
  optimizerLocks: null,
  startingPrompt: null,
};

export function useConnectorView(datasetName: string | null): ConnectorView {
  const [backends, setBackends] = useState<BackendInfo[]>([]);
  const [view, setView] = useState<PipelineView | null>(null);
  const [connector, setConnector] = useState<string | null>(null);
  const [backendType, setBackendType] = useState<string | null>(null);
  const [optimizerLocks, setOptimizerLocks] = useState<OptimizerLocks | null>(null);
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
    setOptimizerLocks(null);
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
          optimizer_locks?: OptimizerLocks | null;
          starting_prompt?: Record<string, unknown> | null;
        };
        if (!cancelled) {
          setView(resp?.view ?? null);
          setConnector(resp?.connector ?? null);
          setBackendType(resp?.pipeline?.backend_type ?? null);
          setOptimizerLocks(resp?.optimizer_locks ?? null);
          setStartingPrompt(resp?.starting_prompt ?? null);
        }
      } catch {
        if (!cancelled) {
          setView(null);
          setConnector(null);
          setBackendType(null);
          setOptimizerLocks(null);
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
      optimizerLocks,
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
    optimizerLocks,
    startingPrompt,
  ]);
}
