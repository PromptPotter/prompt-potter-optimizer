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
import { decodeCyclePath, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { applyChartDefaults } from "@/lib/theme";
import { cx } from "@/lib/cx";
import { Sidebar } from "@/components/shell/Sidebar";
import { SidebarResizer } from "@/components/shell/SidebarResizer";
import { Topbar, type Tab } from "@/components/shell/Topbar";
import { DashboardTab } from "@/components/dashboard/layout/DashboardTab";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageProvider } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
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
// How long the "this no longer exists" notice stays up. Long enough to read after
// glancing back at the tab, short enough that it never becomes furniture — the
// recovery it describes is already complete, so it owes the operator nothing.
const GONE_NOTICE_MS = 8000;

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
    selectCyclePath,
    leafCycleId,
    viewedCandidateId,
    goneAddress,
    dismissGoneNotice,
  } = useWorkspace();

  // ── Per-campaign view memory: remember where the operator was, put them back.
  const { viewFor, recordView } = useViewMemory();

  // RECORD. The navigation axis only — ids, no measurement (`lib/view-memory.tsx`).
  // In an effect because it mirrors React state OUT to an external store, which is what
  // effects are for; `useLocalStorage` writes through `useSyncExternalStore`, so this is a
  // store emit rather than a setState cascade.
  useEffect(() => {
    if (!campaignId || !viewedPath) return;
    recordView(campaignId, {
      viewedPath: encodeCyclePath(viewedPath),
      viewedCandidateId,
    });
  }, [campaignId, viewedPath, viewedCandidateId, recordView]);

  // FORGET + auto-dismiss. Memory is what made the dead address survive a reload —
  // the operator refreshed and landed right back on it — so the record that names
  // it has to go with it. Only the NAVIGATION fields are cleared: for a reaped
  // `.inner/` leaf the root campaign is still alive, and its toggles and lanes are
  // still worth keeping. Then the notice retires itself; it reports a recovery that
  // is already done, so it must not need a click.
  useEffect(() => {
    if (!goneAddress) return;
    const rootCampaign = decodeCyclePath(goneAddress)?.[0]?.campaignId;
    if (rootCampaign) recordView(rootCampaign, { viewedPath: null, viewedCandidateId: null });
    const t = window.setTimeout(dismissGoneNotice, GONE_NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [goneAddress, recordView, dismissGoneNotice]);

  // RESTORE. Clicking a campaign's ROOT row means "open this campaign", and what the
  // operator means by that is where they left it — the inner run they had drilled into,
  // parked on the node they were reading. Any deeper or more specific click is an explicit
  // address and is honored as-is; memory never overrides a live intent.
  //
  // Guarded on the remembered leaf still being a cycle the workspace knows: a fork deleted
  // between visits must open the campaign at its root, not spin on an address that 404s.
  const restoreNavigation = useCallback(
    (path: CyclePath, candidate?: string | null): [CyclePath, string | null] => {
      if (path.length !== 1 || candidate) return [path, candidate ?? null];
      const hop = path[0]!;
      // Only a campaign SWITCH restores. Clicking the VIEWED campaign's own root row goes
      // to the root — that click is the escape hatch, and without it there is none: the
      // RECORD effect above re-records the restored path continuously, so a restore that
      // also fired in-campaign would re-drill forever.
      if (hop.campaignId === campaignId) return [path, null];
      const mem = viewFor(hop.campaignId);
      const remembered = decodeCyclePath(mem.viewedPath ?? "");
      if (!remembered || remembered[0]?.campaignId !== hop.campaignId) return [path, null];
      if (encodeCyclePath(remembered) === encodeCyclePath(path)) return [path, null];
      // Inner hops live in a sandbox and never appear in `/cycles`; the ROOT hop still
      // existing is what makes the whole address resolvable — a fork deleted between
      // visits must open the campaign at its root, not spin on an address that 404s.
      const root = remembered[0]!;
      const known = cycles.some(
        (c) => c.campaign_id === root.campaignId && c.cycle_id === root.cycleId,
      );
      return known ? [remembered, mem.viewedCandidateId] : [path, null];
    },
    [campaignId, viewFor, cycles],
  );
  // The DISPLAY panes (connector, pipeline hero, hard-samples) follow the VIEWED
  // LEAF hop — the same hop the dashboard stream re-roots to — so drilling into an
  // L4 inner loop shows the inner run's connector + samples, not the outer loop
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
  if (goneAddress) {
    // Rides the WORKSPACE's verdict, not the poll's, and wins outright. The
    // recovery already happened — the pin was dropped the moment it was confirmed
    // dead — so `dashState` has since reset onto a different address and would
    // otherwise replace this notice within a frame. The announcement has to
    // outlive the transition it explains, or the view just silently jumps.
    bannerStatus = "gone";
    bannerText = "This campaign no longer exists";
    bannerHint = "It was deleted, or its store was reset — returning to the active run.";
  } else if (noUnit && netDown) {
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
    // Keyed on the LEAF hop, like the dashboard stream — selection scopes the
    // inspector / samples / round files, and those read the leaf.
    <SelectionProvider cycleId={leafCycleId}>
    {/* THE served lineage — ONE fetch owner for every consumer (the forest, the bars,
        the sidebar rows, the L4 samples panel). Rooted at the ROOT hop: the tree's own
        recursion reaches every fork and inner run below it, so drilling in re-addresses
        rather than re-fetching. Sits inside SelectionProvider, whose `sampleSet` is one
        of the masks it composes. */}
    <LineageProvider campaignId={campaignId} cycleId={cycleId}>
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
        onSelectPath={(path, candidate) => {
          selectCyclePath(...restoreNavigation(path, candidate));
          // The tab is the operator's axis — selecting a unit must NOT hijack it
          // (picking a campaign while reading the Chat stays on Chat). The one
          // exception is a check-in: it has no dashboard.json, so Dashboard/Verify
          // would dead-end — send it to Chat, its authoring home. Only a top-level
          // cycle can be a check-in (an inner run is machine-minted and always
          // past authoring), so a descended path never redirects.
          if (path.length > 1) return;
          const hop = path[0]!;
          const checkin = cycles.some(
            (c) =>
              c.campaign_id === hop.campaignId &&
              c.cycle_id === hop.cycleId &&
              c.run_phase === "checkin",
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
    </LineageProvider>
    </SelectionProvider>
  );
}


