"use client";
import { useCallback, useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { fetchCycleFile, fetchPipeline, type HardSamplesScope } from "@/lib/api";
import { postStopCycle } from "@/lib/api/mutations";
import { CycleStreamProvider } from "@/lib/poll";
import { ConnectorProvider } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useDatasetPreview } from "@/lib/hooks/useDatasetPreview";
import { useFetch } from "@/lib/hooks/useFetch";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { applyChartDefaults } from "@/lib/theme";
import { Sidebar } from "@/components/shell/Sidebar";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { StatusAssistant } from "@/components/status/StatusAssistant";
import { TopStrip } from "@/components/dashboard/layout/TopStrip";
import { Lane } from "@/components/dashboard/layout/Lane";
import { LiveStateCard } from "@/components/dashboard/scoring/LiveStateCard";
import { RoundTabsStrip } from "@/components/dashboard/samples/RoundTabsStrip";
import { CyclePicker } from "@/components/shell/CyclePicker";
import { SelectionProvider } from "@/lib/SelectionContext";
import { NowTriad } from "@/components/dashboard/layout/NowTriad";
import { RunErrorBanner } from "@/components/dashboard/layout/RunErrorBanner";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";

import type { PipelineDoc } from "@/components/workflow/types";

// The non-landing surfaces load on demand, not on first paint. The operator
// lands on the dashboard tab; Chat / Files / Verify (and the markdown renderer
// `marked` that rides inside Files) and the Ingest modal each ship as their own
// chunk fetched only when the tab is opened — `ssr: false` since they're
// client-only and there's no SSR under `output: export` anyway.
const ChatPane = dynamic(() => import("@/components/chat/ChatPane").then((m) => m.ChatPane), {
  ssr: false,
  loading: () => <div className="content" aria-busy="true" />,
});
const FilesPane = dynamic(() => import("@/components/tree/FilesPane").then((m) => m.FilesPane), {
  ssr: false,
  loading: () => <div className="content" aria-busy="true" />,
});
const VerifyPane = dynamic(() => import("@/components/verify/VerifyPane").then((m) => m.VerifyPane), {
  ssr: false,
  loading: () => <div className="content" aria-busy="true" />,
});
const IngestPane = dynamic(() => import("@/components/ingest/IngestPane").then((m) => m.IngestPane), {
  ssr: false,
});

export function AppShell() {
  // `campaignId` + `cycleId` are owned by WorkspaceProvider (the single
  // workspace-identity source of truth) — this only forwards them into the
  // dashboard's per-cycle data stream.
  const { campaignId, cycleId } = useWorkspace();
  return (
    <CycleStreamProvider campaignId={campaignId} cycleId={cycleId}>
      <AppShellInner />
    </CycleStreamProvider>
  );
}

function AppShellInner() {
  // Workspace identity comes from the shared context — no local cycle
  // resolution, no independent /sessions/active poll. A `cycle_id` is unique only
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
  // Hard-sample view scope — campaign = only the current campaign's cycles
  // (the default view), dataset = every campaign on this dataset, which is
  // the real series the optimizer's picker follows. Clicking the heat-map
  // badge toggles which sort+series is shown; both are held in memory so
  // the switch is instant. The picker itself always runs on the dataset
  // scope regardless of this toggle — see l1/execute.py round-subset fit.
  const [hardSamplesScope, setHardSamplesScope] = useState<HardSamplesScope>("campaign");
  // Dataset roster + per-sample measurement history for the unit in view.
  // One hook owns the fetch chain — it loads BOTH scope slices per unit, so
  // the scope toggle is a pure in-memory pick (no re-fetch, no cross-scope
  // borrow). A campaign switch shows the prior data marked stale until the
  // new fetch lands — never blanks.
  const {
    datasetName,
    items: datasetItems,
    measuredCount: datasetMeasuredCount,
    unmeasuredCount: datasetUnmeasuredCount,
    splitTest: datasetSplitTest,
    archivePerSample,
    isStale: datasetStale,
  } = useDatasetPreview(campaignId, cycleId, hardSamplesScope);
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
  const { dash, dashRound, isLive, runPhaseResolved } = dashState;

  // One-shot pipeline (topology) lookup. Errors → pipeline stays null (panes
  // that need it render their own empty state); no retry needed for a static read.
  const { data: pipeline } = useFetch<PipelineDoc>(
    () => fetchPipeline().then((p) => p as PipelineDoc),
    [],
  );

  // Cycle title (dataset name) from index.json. Hand-rolled on purpose: it
  // fans one fetch into two state slots (title + started-at) AND must KEEP the
  // prior title across a unit switch — useFetch blanks data on a deps change,
  // which would flash the raw cycle-id hash before the new index.json lands.
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
    <ConnectorProvider datasetName={datasetName}>
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
          // "New campaign" opens the menu directly — pick a dataset / reuse an
          // origin / drop a file — over whatever tab is in view. It is its own
          // self-contained flow (menu → context → ready → Start); do NOT also
          // jump to the Chat tab — that detour buried the menu behind the chat
          // page and read as "nothing happened".
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
        {/* Loud failure surface — sticky, full-width, on every tab. Renders
            null on a healthy run; not gated on cycleId so a server-down state
            with no unit in view still screams. */}
        <CriticalAlertBanner
          bannerStatus={bannerStatus}
          bannerText={bannerText}
          bannerHint={bannerHint}
          dash={dash}
          runPhaseResolved={runPhaseResolved}
          onOpenFiles={() => setTab("files")}
          onStopCampaign={
            campaignId && cycleId
              ? () => void postStopCycle(campaignId, cycleId)
              : undefined
          }
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
            onMinted={(sel) => selectCycle(sel.campaignId, sel.cycleId)}
          />
        ) : tab === "dashboard" ? (
          <div className="content" id="content-dashboard">
            <div className="dash-spine-narrow">
              <header className="dash-hero">
                <div className="page-header">
                  <div className="breadcrumb">
                    Campaign »{" "}
                    <CyclePicker />
                  </div>
                </div>
              </header>
            </div>
            <div className="dash-spine-narrow">
              <RunErrorBanner dash={dash} />
              {/* TopStrip + RoundTabsStrip share one row so the round
                  axis (LIVE pill + completed-round circles) sits beside
                  the headline KPIs the operator scans first. */}
              <div className="dash-top-row">
                <TopStrip dash={dash} dashRound={dashRound} runPhase={runPhaseResolved} />
                <RoundTabsStrip dash={dash} isLive={isLive} />
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
      </main>
      {/* Mounted only while open so its chunk (+ ingest wizard deps) stays off
          first paint — IngestPane already hard-returns null when closed, so
          gating the mount is behaviour-identical. */}
      {newCampaignOpen && (
        <IngestPane
          open
          onClose={() => setNewCampaignOpen(false)}
          onMinted={(sel) => {
            // The from-draft mint returns the new (campaign, cycle) — select it
            // now rather than waiting on the 2 s workspace poll.
            selectCycle(sel.campaignId, sel.cycleId);
          }}
        />
      )}
    </div>
    </ConnectorProvider>
    </SelectionProvider>
  );
}


