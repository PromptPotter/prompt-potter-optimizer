"use client";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import dynamic from "next/dynamic";
import { fetchCycleFile, type HardSamplesScope } from "@/lib/api";
import { postPauseCycle } from "@/lib/api/mutations";
import { CycleStreamProvider } from "@/lib/poll";
import { ConnectorProvider } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { useDatasetPreview } from "@/lib/hooks/useDatasetPreview";
import { useLeafCycleIndex } from "@/lib/hooks/useLeafCycleIndex";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { applyChartDefaults } from "@/lib/theme";
import { cx } from "@/lib/cx";
import { Sidebar } from "@/components/shell/Sidebar";
import { SidebarResizer } from "@/components/shell/SidebarResizer";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { DashboardTab } from "@/components/dashboard/layout/DashboardTab";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageOverlayProvider } from "@/lib/lineage-overlay";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteBar } from "@/components/shell/RemoteBar";

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
const CheckinReopenPane = dynamic(
  () => import("@/components/ingest/CheckinReopenPane").then((m) => m.CheckinReopenPane),
  { ssr: false, loading: () => <div className="content" aria-busy="true" /> },
);

// Sidebar resize bounds. Default matches the CSS base (.shell{--sidebar-width}).
const SIDEBAR_DEFAULT = 200;
const SIDEBAR_MIN = 160;
const SIDEBAR_MAX = 480;

export function AppShell() {
  // `campaignId` + `cycleId` are owned by WorkspaceProvider (the single
  // workspace-identity source of truth) — this only forwards them into the
  // dashboard's per-cycle data stream.
  const { viewedPath } = useWorkspace();
  // The dashboard stream re-roots to the viewed path's LEAF hop (an inner loop
  // when descended). Chat/dataset panels bind to the ROOT hop instead (the
  // `campaignId`/`cycleId` exports), so the conversation stays on the outer
  // thread while the dashboard follows an inner cycle.
  return (
    <CycleStreamProvider path={viewedPath}>
      <AppShellInner />
    </CycleStreamProvider>
  );
}

function AppShellInner() {
  // Workspace identity comes from the shared context — no local cycle
  // resolution, no independent /sessions/active poll. A `cycle_id` is unique only
  // within its campaign, so `campaignId` rides alongside `cycleId`.
  const {
    viewedPath,
    campaignId,
    cycleId,
    datasetName,
    activeError,
    cyclesError,
    cyclesLoaded,
    cycles,
    selectCycle,
  } = useWorkspace();
  // The DISPLAY panes (connector, pipeline hero, hard-samples) follow the VIEWED
  // LEAF hop — the same hop the dashboard stream re-roots to — so drilling into an
  // L4 inner loop shows the inner run's connector + samples, not the outer meta
  // pipeline. The CHAT THREAD (live feed, control verbs, session, Files, checkin)
  // stays on the root hop below. At depth 1 leaf == root, so every leaf value
  // equals its root counterpart and the top-level view is unchanged. `leafCreatedAt`
  // is the inner cycle's own start time (null at depth 1 → keep the root value).
  const { datasetName: leafDatasetName, createdAt: leafCreatedAt } = useLeafCycleIndex(
    viewedPath,
    datasetName,
  );
  // Replit-style sub-tabs (Chat / Dashboard / Verify / Files) scoped to the
  // currently-selected cycle. Default = chat: that's where new cycles get
  // conceived and where the conversational interface lives. (The
  // CriticalAlertBanner also jumps to Files on a failure state.)
  const [tab, setTab] = useState<Tab>("chat");
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  // Bumped each time "New campaign" is hit while the chat tab is in view —
  // ChatPane watches it and resets the thread to its empty first-run state
  // (the inline entry point, distinct from the modal the other tabs open).
  const [newCampaignTick, setNewCampaignTick] = useState(0);
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
    items: datasetItems,
    measuredCount: datasetMeasuredCount,
    unmeasuredCount: datasetUnmeasuredCount,
    splitTest: datasetSplitTest,
    archivePerSample,
    isStale: datasetStale,
    error: datasetError,
  } = useDatasetPreview(viewedPath, leafDatasetName, hardSamplesScope);
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
  // Sidebar width — operator-adjustable via the drag handle, persistent.
  // Default matches the CSS base (200px); clamped to [SIDEBAR_MIN, SIDEBAR_MAX].
  // Applied as an inline --sidebar-width only while expanded, so the collapsed
  // rail's 36px class rule still wins.
  const [sidebarWidth, setSidebarWidth] = useLocalStorage<number>(
    "promptpotter.sidebar.width",
    SIDEBAR_DEFAULT,
    {
      serialize: String,
      deserialize: (raw) => {
        const n = parseInt(raw, 10);
        return Number.isFinite(n) ? n : SIDEBAR_DEFAULT;
      },
    },
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

  // Single-ingress dashboard read, kept here only for the status-banner
  // derivation below. Every dashboard surface self-sources its own live state
  // via `useDashboard()`/`useCycleStream()`, so nothing is threaded from here.
  const dashState = useDashboard();

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

  // The selected cycle's run phase, read off the cycle list. A `checkin` campaign
  // has no dashboard.json (it hasn't run), so rendering the dashboard/chat/verify
  // panes over it would dead-end on "warming". Instead we show the re-open
  // authoring surface — the honest affordance (frontend-surface-contract.md).
  const selectedCheckin =
    !!campaignId &&
    cycles.some(
      (c) =>
        c.campaign_id === campaignId && c.cycle_id === cycleId && c.run_phase === "checkin",
    );
  // The check-in authoring surface lives on the CHAT tab — its home. Scoping the
  // takeover here (rather than overriding every tab) keeps Dashboard/Verify and the
  // "New campaign" button reachable, so a selected check-in never traps navigation.
  const showCheckin = selectedCheckin && tab === "chat";

  return (
    <SelectionProvider cycleId={cycleId}>
    {/* The campaign lineage fetch + its mask/lens divergence overlay — owned once
        at the shell root (inside CycleStreamProvider + SelectionProvider it reads
        from), consumed by the lineage card and the fitness panel. */}
    <LineageOverlayProvider campaignId={campaignId}>
    <ConnectorProvider datasetName={leafDatasetName}>
    <div
      className={cx(
        "shell",
        sidebarCollapsed && "sidebar-collapsed",
        sidebarMobileOpen && "sidebar-mobile-open",
      )}
      style={
        sidebarCollapsed
          ? undefined
          : ({ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties)
      }
    >
      {/* First focusable element — lets keyboard users jump the sidebar +
          topbar straight to the main content. Off-screen until focused. */}
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar
        onSelectCycle={(cmp, cyc) => {
          selectCycle(cmp, cyc);
          // The tab is the operator's axis — selecting a unit must NOT hijack it
          // (picking a campaign while reading the Chat stays on Chat). The one
          // exception is a check-in: it has no dashboard.json, so Dashboard/Verify
          // would dead-end — send it to Chat, its authoring home.
          const checkin = cycles.some(
            (c) => c.campaign_id === cmp && c.cycle_id === cyc && c.run_phase === "checkin",
          );
          if (checkin) setTab("chat");
        }}
        onNewCycle={() => {
          // Two entry points, picked by the view in front of the operator. On the
          // chat tab, "New campaign" resets the thread in place to its empty
          // first-run state — no modal over the chat (that detour buried the menu
          // and read as "nothing happened"). On any other tab — INCLUDING while a
          // check-in owns the chat tab (its takeover replaces ChatPane, so the
          // in-place reset has no consumer) — open the self-contained modal, which
          // overlays everything and is the escape hatch out of the check-in.
          if (tab === "chat" && !showCheckin) setNewCampaignTick((t) => t + 1);
          else setNewCampaignOpen(true);
        }}
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
      />
      {!sidebarCollapsed && (
        <SidebarResizer
          width={sidebarWidth}
          setWidth={setSidebarWidth}
          min={SIDEBAR_MIN}
          max={SIDEBAR_MAX}
        />
      )}
      {/* Mobile drawer backdrop — only rendered on mobile via CSS. Tap
          dismisses the open drawer. */}
      <div
        className="sidebar-backdrop"
        aria-hidden="true"
        onClick={() => setSidebarMobileOpen(false)}
      />
      <main className="main" id="main-content" tabIndex={-1}>
        <Topbar
          tab={tab}
          onTabChange={setTab}
          onMenuToggle={() => setSidebarMobileOpen((v) => !v)}
        />
        {/* Loud failure surface — sticky, full-width, on every tab. Renders
            null on a healthy run; not gated on cycleId so a server-down state
            with no unit in view still screams. */}
        <CriticalAlertBanner
          bannerStatus={bannerStatus}
          bannerText={bannerText}
          bannerHint={bannerHint}
          onOpenFiles={() => setTab("files")}
          onPauseCampaign={
            campaignId && cycleId
              ? () => void postPauseCycle(campaignId, cycleId)
              : undefined
          }
        />
        {showCheckin && campaignId ? (
          <CheckinReopenPane
            campaignId={campaignId}
            onStarted={(sel) => selectCycle(sel.campaignId, sel.cycleId)}
          />
        ) : tab === "chat" ? (
          <ChatPane
            datasetTitle={datasetTitle}
            cycleStartedAt={leafCreatedAt ?? cycleStartedAt}
            datasetName={leafDatasetName}
            datasetItems={datasetItems}
            datasetMeasuredCount={datasetMeasuredCount}
            datasetUnmeasuredCount={datasetUnmeasuredCount}
            datasetSplitTest={datasetSplitTest}
            archivePerSample={archivePerSample}
            datasetStale={datasetStale}
            datasetError={datasetError}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={setHardSamplesScope}
            newCampaignTick={newCampaignTick}
            onMinted={(sel) => selectCycle(sel.campaignId, sel.cycleId)}
          />
        ) : tab === "dashboard" ? (
          <DashboardTab />
        ) : tab === "files" ? (
          <FilesPane campaignId={campaignId} cycleId={cycleId} />
        ) : (
          <VerifyPane />
        )}
      </main>
      {/* Global remote — a bottom-fixed hovering pill, present on every tab while
          a cycle is live (play/pause/stop/skip + round/spend + babysat tag).
          Self-sources identity + live state; renders null when idle/terminal. */}
      <RemoteBar />
      {/* Mounted only while open so its chunk (+ ingest wizard deps) stays off
          first paint — IngestPane already hard-returns null when closed, so
          gating the mount is behaviour-identical. */}
      {newCampaignOpen && (
        <IngestPane
          open
          onClose={() => setNewCampaignOpen(false)}
          onMinted={(sel) => {
            // start-checkin returns the (campaign, cycle) — select it now rather
            // than waiting on the 2 s workspace poll.
            selectCycle(sel.campaignId, sel.cycleId);
          }}
        />
      )}
    </div>
    </ConnectorProvider>
    </LineageOverlayProvider>
    </SelectionProvider>
  );
}


