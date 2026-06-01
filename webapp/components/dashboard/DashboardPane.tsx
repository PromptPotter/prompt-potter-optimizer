"use client";
import { useCallback, useEffect, useState } from "react";
import { fetchCycleFile, fetchPipeline, type HardSamplesScope } from "@/lib/api";
import { CycleStreamProvider } from "@/lib/poll";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useDatasetPreview } from "@/lib/useDatasetPreview";
import { useLocalStorage } from "@/lib/useLocalStorage";
import { applyChartDefaults } from "@/lib/theme";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusAssistant } from "@/components/status/StatusAssistant";
import { RunTelemetry } from "@/components/status/RunTelemetry";
import { ConsolePane } from "@/components/console/ConsolePane";
import { TopStrip } from "./TopStrip";
import { ChatPane } from "./ChatPane";
import { Lane } from "./Lane";
import { LiveStateCard } from "./LiveStateCard";
import { RoundTabsStrip } from "./round-samples/RoundTabsStrip";
import { CyclePicker } from "./CyclePicker";
import { SelectionProvider } from "./SelectionContext";
import { IngestPane } from "./IngestPane";
import { NowTriad } from "./NowTriad";
import { RunErrorBanner } from "./RunErrorBanner";
import { FilesPane } from "@/components/tree/FilesPane";
import { VerifyPane } from "@/components/verify/VerifyPane";

import type { PipelineDoc } from "@/components/workflow/types";

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
  // Replit-style sub-tabs (Chat / Dashboard / Verify) scoped to the
  // currently-selected cycle. Files is reachable only via StatusAssistant's
  // "Open files" link — not exposed on the topbar. Default = chat: that's
  // where new cycles get conceived and where the conversational interface
  // lives.
  const [tab, setTab] = useState<Tab>("chat");
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const [datasetTitle, setDatasetTitle] = useState<string | null>(null);
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<PipelineDoc | null>(null);
  // Hard-sample view scope — campaign = only the current campaign's cycles
  // (the default view), dataset = every campaign on this dataset, which is
  // the real series the optimizer's picker follows. Clicking the heat-map
  // badge toggles which sort+series is shown; both are held in memory so
  // the switch is instant. The picker itself always runs on the dataset
  // scope regardless of this toggle — see l1/execute.py round-subset fit.
  const [hardSamplesScope, setHardSamplesScope] = useState<HardSamplesScope>("campaign");
  // Dataset roster + per-sample measurement history for the unit in view.
  // One hook owns the fetch chain. Cached per (unit, scope); a campaign or
  // scope switch shows the prior data marked stale until the new fetch
  // lands — never blanks. `dataVersion` is a monotonic stamp on each fresh
  // load; downstream components key per-row memos on it.
  const {
    datasetName,
    items: datasetItems,
    measuredCount: datasetMeasuredCount,
    unmeasuredCount: datasetUnmeasuredCount,
    splitTest: datasetSplitTest,
    archivePerSample,
    isStale: datasetStale,
  } = useDatasetPreview(campaignId, cycleId, hardSamplesScope);
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
  // Mobile-only drawer state. Below --bp-md the sidebar is hidden by
  // default and the topbar shows a hamburger; tapping the hamburger
  // (or the backdrop) toggles this. Resets on tab switch via the
  // render-phase guarded reset (webapp/CLAUDE.md "State reset on prop
  // change") — running during render avoids the post-paint flash of
  // the prior-tab drawer state.
  const [sidebarMobileOpen, setSidebarMobileOpen] = useState(false);
  const [prevTab, setPrevTab] = useState(tab);
  if (tab !== prevTab) {
    setPrevTab(tab);
    setSidebarMobileOpen(false);
  }

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

  // Apply chart defaults once on mount; theme flips re-apply via applyTheme
  // (lib/theme.ts) and broadcast via useThemeVersion to subscribed canvases.
  useEffect(() => {
    applyChartDefaults();
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
    <div
      className={`shell${tab === "chat" ? " chat-mode sidebar-hidden" : sidebarCollapsed ? " sidebar-collapsed" : ""}${sidebarMobileOpen ? " sidebar-mobile-open" : ""}`}
    >
      {/* First focusable element — lets keyboard users jump the sidebar +
          topbar straight to the main content. Off-screen until focused. */}
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar
        onSelectCycle={(cmp, cyc) => {
          selectCycle(cmp, cyc);
          setTab("dashboard");
        }}
        onNewCycle={() => {
          // New campaigns are conceived on the Chat page — jump there first so
          // the ingest popup appears in context, not as a detached window.
          setTab("chat");
          setNewCampaignOpen(true);
        }}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
      />
      {/* Mobile drawer backdrop — only rendered on mobile via CSS. Tap
          dismisses the open drawer. */}
      {tab !== "chat" ? (
        <div
          className="sidebar-backdrop"
          aria-hidden="true"
          onClick={() => setSidebarMobileOpen(false)}
        />
      ) : null}
      <main className="main" id="main-content" tabIndex={-1}>
        <Topbar
          tab={tab}
          onTabChange={setTab}
          onMenuToggle={tab !== "chat" ? () => setSidebarMobileOpen((v) => !v) : undefined}
        />
        {cycleId ? (
          <StatusAssistant
            status={bannerStatus}
            statusText={bannerText}
            statusHint={bannerHint}
            termKey={dashState.termKey}
            onOpenFiles={() => setTab("files")}
          />
        ) : null}
        {tab === "chat" ? (
          <ChatPane
            campaignId={campaignId}
            cycleId={cycleId}
            sessionId={sessionId}
            datasetTitle={datasetTitle}
            dash={dash}
            isLive={isLive}
            dashRound={dashRound}
            cycleStartedAt={cycleStartedAt}
            datasetName={datasetName}
            datasetItems={datasetItems}
            datasetMeasuredCount={datasetMeasuredCount}
            datasetUnmeasuredCount={datasetUnmeasuredCount}
            datasetSplitTest={datasetSplitTest}
            archivePerSample={archivePerSample}
            datasetStale={datasetStale}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={setHardSamplesScope}
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
                <RunErrorBanner dash={dash} />
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
                  onSelectCycle={selectCycle}
                  isLive={isLive}
                />
              </div>
            </Lane>
            <Lane id="livestate" title="Live state" subtitle="Raw dashboard.json + trend + score frequency" defaultOpen>
              <div className="dash-spine-narrow">
                <LiveStateCard dash={dash} />
              </div>
            </Lane>
          </div>
        ) : tab === "files" ? (
          <FilesPane campaignId={campaignId} cycleId={cycleId} />
        ) : (
          <VerifyPane />
        )}
        <ConsolePane
          campaignId={campaignId}
          cycleId={cycleId}
          headSlot={
            cycleId ? (
              <RunTelemetry
                campaignId={campaignId}
                cycleId={cycleId}
                dash={dash}
                isLive={isLive}
              />
            ) : null
          }
        />
      </main>
      <IngestPane
        open={newCampaignOpen}
        onClose={() => setNewCampaignOpen(false)}
        onMinted={(sel) => {
          // Fresh-CSV / from-draft mint returns the new (campaign, cycle) —
          // select it now rather than waiting on the 2 s workspace poll. The
          // existing-Origin and benchmark paths return null (the poll snaps).
          if (sel) selectCycle(sel.campaignId, sel.cycleId);
        }}
      />
    </div>
    </SelectionProvider>
  );
}


