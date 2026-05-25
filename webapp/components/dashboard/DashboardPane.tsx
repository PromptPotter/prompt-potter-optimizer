"use client";
import { useCallback, useEffect, useState } from "react";
import {
  fetchCycleFile,
  fetchDatasetPipeline,
  fetchPipeline,
  type HardSamplesScope,
} from "@/lib/api";
import { CycleStreamProvider } from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useDatasetPreview } from "@/lib/useDatasetPreview";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { applyChartDefaults } from "@/lib/theme";
import { Chart as ChartJS } from "chart.js";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusBar } from "@/components/status/StatusBar";
import { ConsolePane } from "@/components/console/ConsolePane";
import { TopStrip } from "./TopStrip";
import { ChatPane } from "./ChatPane";
import { Lane } from "./Lane";
import { LiveStateCard } from "./LiveStateCard";
import { RoundTabsStrip } from "./round-samples/RoundTabsStrip";
import { CyclePicker } from "./CyclePicker";
import { SelectionProvider } from "./SelectionContext";
import { NowTriad } from "./NowTriad";
import { FilesPane } from "@/components/tree/FilesPane";
import { LeveragePanel } from "@/components/leverage/LeveragePanel";
import { ComparePane } from "@/components/compare/ComparePane";
import { VerifyPane } from "@/components/verify/VerifyPane";

import type { PipelineDoc, PipelineView } from "@/components/workflow/types";

export function DashboardPane() {
  // `campaignId` + `cycleId` are owned by WorkspaceProvider (the single
  // workspace-identity source of truth) — this only forwards them into the
  // dashboard's per-cycle data stream.
  const { campaignId, cycleId } = useWorkspace();
  return (
    <CycleStreamProvider campaignId={campaignId} cycleId={cycleId}>
      <DashboardPaneInner />
    </CycleStreamProvider>
  );
}

function DashboardPaneInner() {
  // Workspace identity comes from the shared context — no local cycle
  // resolution, no independent /active poll. A `cycle_id` is unique only
  // within its campaign, so `campaignId` rides alongside `cycleId`.
  const {
    campaignId,
    cycleId,
    sessionId,
    activeError,
    cyclesError,
    cyclesLoaded,
    cycles,
    selectCycle,
  } = useWorkspace();
  // Replit-style sub-tabs (Chat / Dashboard / Files) scoped to the
  // currently-selected cycle. The sidebar is persistent across all
  // three. Default = chat: that's where new cycles get conceived and
  // where the conversational interface lives. Flipping the default to
  // "dashboard" on initial mount tripped React #185 — the hero/fitness
  // chain doesn't handle null→non-null cycleId during the first paint;
  // chat dodges that path. Revisit when we untangle the dashboard
  // first-render sequence.
  const [tab, setTab] = useState<Tab>("chat");
  const [datasetTitle, setDatasetTitle] = useState<string | null>(null);
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineDoc | null>(null);
  // Target connector pipeline view — what the dataset's `pipelines.default`
  // actually wires up. Drives the data-driven chat-pane hero (N=1 keeps the
  // single-LLM chip look; N≥2 renders dot+outside-label chain). Fetched once
  // per datasetName change.
  const [targetPipelineView, setTargetPipelineView] = useState<PipelineView | null>(null);
  // Original-cased connector name (e.g. "TermNorm Local") rendered as a
  // small uppercase tag inside the multi-node chip. Same fetch as the view.
  const [targetConnector, setTargetConnector] = useState<string | null>(null);
  // Hard-sample view scope — campaign = only the current campaign's cycles
  // (the default view), dataset = every campaign on this dataset, which is
  // the real series the optimizer's picker follows. Clicking the heat-map
  // badge toggles which sort+series is shown; both are held in memory so
  // the switch is instant. The picker itself always runs on the dataset
  // scope regardless of this toggle — see l1/execute.py round-subset fit.
  const [hardSamplesScope, setHardSamplesScope] = useState<HardSamplesScope>("campaign");
  // Dataset roster + per-sample measurement history for the unit in view.
  // One hook owns the fetch chain and blanks cleanly on a unit switch.
  const {
    datasetName,
    items: datasetItems,
    measuredCount: datasetMeasuredCount,
    unmeasuredCount: datasetUnmeasuredCount,
    splitTest: datasetSplitTest,
    archivePerSample,
  } = useDatasetPreview(campaignId, cycleId, hardSamplesScope);
  const [themeKey, setThemeKey] = useState<string>("init");
  // Edit mode — off by default; gates Stop run + Fork-from-here. Never
  // persisted across reloads (no URL param, no localStorage) so the
  // operator never finds risky affordances quietly enabled.
  const [editMode, setEditMode] = useState(false);
  // Sidebar collapse — user-driven, persistent across reloads. Default
  // expanded; once the user collapses it, that sticks until they toggle
  // again. Tab switches never touch this state — that's the whole point
  // of the manual control.
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage<boolean>(
    "promptpotter.sidebar.collapsed",
    false,
    { serialize: (v) => (v ? "1" : "0"), deserialize: (raw) => raw === "1" },
  );
  const toggleSidebar = useCallback(
    () => setSidebarCollapsed((prev) => !prev),
    [setSidebarCollapsed],
  );

  // Single-ingress dashboard read. `useDashboard` wraps `useCycleStream`
  // and exposes the derived round number — no component re-runs `roundOf`
  // on its own, no second snapshot path.
  const dashState = useDashboard();
  const { dash, dashRound, isLive } = dashState;

  // One-shot pipeline (topology) lookup
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const p = await fetchPipeline();
        if (!cancelled) setPipeline(p as PipelineDoc);
      } catch {
        /* ignore */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // The active dataset is bound at cycle-identity time, but a unit switch
  // changes it — drop the stale target-pipeline view render-phase so the
  // effect below refetches cleanly.
  const [prevDatasetName, setPrevDatasetName] = useState(datasetName);
  if (datasetName !== prevDatasetName) {
    setPrevDatasetName(datasetName);
    setTargetPipelineView(null);
    setTargetConnector(null);
  }

  // Target connector pipeline view per active dataset. Same one-shot pattern
  // as the optimizer pipeline — the dataset's `pipelines.default` is bound
  // at cycle-identity hash time and doesn't mutate mid-loop.
  useEffect(() => {
    if (!datasetName) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = (await fetchDatasetPipeline(datasetName)) as {
          view?: PipelineView | null;
          connector?: string | null;
        };
        if (!cancelled) {
          setTargetPipelineView(resp?.view ?? null);
          setTargetConnector(resp?.connector ?? null);
        }
      } catch {
        if (!cancelled) {
          setTargetPipelineView(null);
          setTargetConnector(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [datasetName]);

  // Cycle title (dataset name) from index.json
  useEffect(() => {
    if (!campaignId || !cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycleFile(campaignId, cycleId, "cycle", "index.json");
        const idx = r.content ? JSON.parse(r.content) : {};
        if (!cancelled) {
          setDatasetTitle(
            idx.header?.dataset_name || idx.dataset_name || idx.cycle_id || cycleId,
          );
          setCycleStartedAt(typeof idx.created_at === "string" ? idx.created_at : null);
        }
      } catch {
        if (!cancelled) setDatasetTitle(cycleId);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [campaignId, cycleId]);

  // Re-applies chart defaults on theme swap; the themeKey bump forces all
  // chart components to remount so they pick up the new --color-* values.
  const onThemeChange = useCallback(() => {
    applyChartDefaults(ChartJS);
    setThemeKey(`t-${Date.now()}`);
  }, []);

  // Apply chart defaults once on mount.
  useEffect(() => {
    applyChartDefaults(ChartJS);
  }, []);

  // Status banner with no unit in view: a network failure and a genuinely
  // empty workspace both end with no cycleId — but only one means the
  // operator should go check the server. Tell them apart. Order matters: a
  // down server also reports zero cycles, so the netDown branch goes first.
  const noUnit = !cycleId;
  const netDown = Boolean(activeError || cyclesError);
  let bannerStatus = dashState.status;
  let bannerText = dashState.statusText;
  let bannerHint = dashState.statusHint;
  if (noUnit && netDown) {
    bannerStatus = "offline";
    bannerText = "Server unreachable — retrying";
    bannerHint = activeError ?? cyclesError ?? "";
  } else if (noUnit && cyclesLoaded && cycles.length === 0) {
    bannerStatus = "offline";
    bannerText = "No active campaign yet";
    bannerHint =
      "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.";
  }

  return (
    <SelectionProvider cycleId={cycleId}>
    <div className={`shell${tab === "chat" ? " chat-mode sidebar-hidden" : sidebarCollapsed ? " sidebar-collapsed" : ""}`}>
      <Sidebar
        onSelectCycle={(cmp, cyc) => {
          selectCycle(cmp, cyc);
          setTab("dashboard");
        }}
        onNewCycle={() => setTab("chat")}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
      />
      <main className="main">
        <Topbar tab={tab} onTabChange={setTab} onThemeChange={onThemeChange} />
        <StatusBar
          status={bannerStatus}
          statusText={bannerText}
          statusHint={bannerHint}
          termKey={dashState.termKey}
          campaignId={campaignId}
          cycleId={cycleId}
          dash={dash}
          isLive={isLive}
          onOpenFiles={() => setTab("files")}
        />
        {tab === "chat" ? (
          <ChatPane
            cycleId={cycleId}
            sessionId={sessionId}
            datasetTitle={datasetTitle}
            dash={dash}
            isLive={isLive}
            dashRound={dashRound}
            cycleStartedAt={cycleStartedAt}
            themeKey={themeKey}
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetMeasuredCount={datasetMeasuredCount}
            datasetUnmeasuredCount={datasetUnmeasuredCount}
            datasetSplitTest={datasetSplitTest}
            archivePerSample={archivePerSample}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={setHardSamplesScope}
            targetPipelineView={targetPipelineView}
            targetConnector={targetConnector}
          />
        ) : tab === "dashboard" ? (
          <div className="content" id="content-dashboard">
            <div className="dash-spine-narrow">
              <header className="dash-hero">
                <div className="page-header">
                  <div className="breadcrumb">
                    Campaign »{" "}
                    <CyclePicker />
                    <span className="cycle-toolbar">
                      <button
                        type="button"
                        className={`edit-mode-toggle${editMode ? " on" : ""}`}
                        onClick={() => setEditMode(!editMode)}
                        aria-pressed={editMode}
                        title={editMode ? "Editing — risky actions exposed" : "Read-only view"}
                      >
                        {editMode ? "● Editing" : "Edit"}
                      </button>
                    </span>
                  </div>
                </div>
              </header>
            </div>
            <Lane id="now" title="Now" subtitle="What's running right now" defaultOpen>
              <div className="dash-spine-narrow">
                {/* TopStrip + RoundTabsStrip share one row so the round
                    axis (LIVE pill + completed-round circles) sits beside
                    the headline KPIs the operator scans first. */}
                <div className="dash-top-row">
                  <TopStrip dash={dash} dashRound={dashRound} />
                  <RoundTabsStrip dash={dash} />
                </div>
              </div>
              <div className="dash-spine-narrow">
                <NowTriad
                  dash={dash}
                  dashRound={dashRound}
                  status={dashState.status}
                  pipeline={pipeline}
                  campaignId={campaignId}
                  cycleId={cycleId}
                  themeKey={themeKey}
                  onSelectCycle={selectCycle}
                  isLive={isLive}
                />
              </div>
            </Lane>
            <Lane id="livestate" title="Live state" subtitle="Raw dashboard.json + trend + score frequency" defaultOpen>
              <div className="dash-spine-narrow">
                <LiveStateCard dash={dash} themeKey={themeKey} />
              </div>
            </Lane>
          </div>
        ) : tab === "files" ? (
          <FilesPane campaignId={campaignId} cycleId={cycleId} />
        ) : tab === "compare" ? (
          <ComparePane themeKey={themeKey} />
        ) : tab === "leverage" ? (
          <LeveragePanel />
        ) : (
          <VerifyPane />
        )}
        <ConsolePane campaignId={campaignId} cycleId={cycleId} />
      </main>
    </div>
    </SelectionProvider>
  );
}


