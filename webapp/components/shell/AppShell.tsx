"use client";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import dynamic from "next/dynamic";
import { fetchCycleFile, type HardSampleOrder, type HardSamplesScope } from "@/lib/api";
import { postPauseCycle } from "@/lib/api/commands";
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
import type { Tab } from "@/lib/view-tab";
import { Sidebar } from "@/components/shell/Sidebar";
import { SidebarResizer } from "@/components/shell/SidebarResizer";
import { JobsDock } from "@/components/shell/JobsDock";
import { MobileAppBar } from "@/components/shell/MobileAppBar";
import { DashboardTab } from "@/components/dashboard/layout/DashboardTab";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageProvider } from "@/lib/lineage";
import { useViewMemory } from "@/lib/view-memory";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteControl } from "@/components/shell/RemoteControl";

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
  // The per-campaign view (Chat / Dashboard / Verify / Files), scoped to the
  // currently-selected cycle. Default = chat: that's where new cycles get
  // conceived and where the conversational interface lives. The sidebar's ViewNav
  // renders it on a desktop, the mobile app bar on a phone.
  const [tab, setTab] = useState<Tab>("chat");
  // Which of the two PHONE screens is showing. `false` = the campaign screen,
  // which is also the desktop-equivalent default and the public landing surface
  // (the Chat pane) — booting an anon phone visitor onto a list that reads "Sign
  // in to see your campaigns" would bury the thing they came for. Inert above
  // --bp-md: no desktop rule reads the class.
  const [listScreen, setListScreen] = useState(false);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  // Bumped each time "New campaign" is hit while the chat tab is in view —
  // ChatPane watches it and resets the thread to its empty first-run state
  // (the inline entry point, distinct from the modal the other tabs open).
  const [newCampaignTick, setNewCampaignTick] = useState(0);
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
  // Hard-sample view scope — campaign = only the current campaign's cycles
  // (the default view), dataset = every campaign on this dataset, which is
  // the real series the optimizer's picker follows. Clicking the heat-map
  // badge toggles which sort+series is shown; both are held in memory so
  // the switch is instant. The picker itself always runs on the dataset
  // scope regardless of this toggle — see l1/execute.py round-subset fit.
  const [hardSamplesScope, setHardSamplesScope] = useState<HardSamplesScope>("campaign");
  // `null` = send no override and let the server resolve the dataset's declared
  // `hard_sample_order`; the browser must never restate that default. What the control
  // DISPLAYS is the served echo below, never this.
  const [hardSampleOrder, setHardSampleOrder] = useState<HardSampleOrder | null>(null);
  // Dataset roster + per-sample measurement history for the unit in view.
  // One hook owns the fetch chain — it fetches ONE (unit, scope) slice at a time,
  // the one in view, and keeps each it has fetched, so flipping the toggle back is
  // a pure in-memory pick and no slice is borrowed across scopes. A campaign switch
  // shows the prior data marked stale until the new fetch lands — never blanks.
  const {
    items: datasetItems,
    measuredCount: datasetMeasuredCount,
    unmeasuredCount: datasetUnmeasuredCount,
    splitTest: datasetSplitTest,
    order: datasetOrder,
    archivePerSample,
    totals: datasetTotals,
    isStale: datasetStale,
    error: datasetError,
  } = useDatasetPreview(viewedPath, leafDatasetName, hardSamplesScope, hardSampleOrder);
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
  // Whether the sidebar's `› more` group (Verify / Files) is expanded — the
  // operator's own preference, so localStorage beside the other two sidebar keys.
  // NOT view-memory: that record is per-campaign with TTL + LRU eviction, so a
  // deliberate preference would flap on every campaign switch and expire on its
  // own. ViewNav derives the RENDERED state from this plus the active view, so
  // there is nothing here to keep in sync.
  const [moreOpen, setMoreOpen] = useLocalStorage<boolean>(
    "promptpotter.sidebar.more",
    false,
    { serialize: (v) => (v ? "1" : "0"), deserialize: (raw) => raw === "1" },
  );
  const toggleMore = useCallback(() => setMoreOpen((prev) => !prev), [setMoreOpen]);

  // The ONE writer of `tab`. Choosing a view means "show me this campaign", so
  // leaving the phone's list screen rides along here rather than at every call site
  // that switches views — which is also why no render-phase reset is needed: the
  // invariant is structural, leaving no derived state to correct.
  const openView = useCallback((t: Tab) => {
    setTab(t);
    setListScreen(false);
  }, []);

  // Every mint / re-open path lands the operator ON the thing it just created,
  // which on a phone means leaving the list screen. One helper so a fourth mint
  // path can't forget.
  const selectAndOpen = useCallback(
    (sel: { campaignId: string; cycleId: string }) => {
      selectCycle(sel.campaignId, sel.cycleId);
      setListScreen(false);
    },
    [selectCycle],
  );

  // Single-ingress dashboard read, kept here only for the status-banner
  // derivation below. Every dashboard surface self-sources its own live state
  // via `useDashboard()`/`useCycleStream()`, so nothing is threaded from here.
  const dashState = useDashboard();

  // The cycle's start stamp from index.json — the burn-rate denominator behind the remote
  // strip's ETA. Hand-rolled on purpose: it must KEEP the prior value across a unit switch,
  // where useFetch blanks data on a deps change and the ETA would flash "—" every time.
  // It used to fan into a second slot for a dataset title; the only reader of that was the
  // chat job-bar, and the remote strip reads the served `dataset_name` off the cycle list.
  useEffect(() => {
    if (!campaignId || !cycleId) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetchCycleFile(campaignId, cycleId, "cycle", "index.json");
        const idx = r.content ? JSON.parse(r.content) : {};
        if (!cancelled) {
          setCycleStartedAt(typeof idx.created_at === "string" ? idx.created_at : null);
        }
      } catch {
        // Leave the prior stamp standing — a failed read is not a new cycle.
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
        listScreen && "mobile-list",
      )}
      style={
        sidebarCollapsed
          ? undefined
          : ({ "--sidebar-width": `${sidebarWidth}px` } as CSSProperties)
      }
    >
      {/* First focusable element — lets keyboard users jump the sidebar straight
          to the main content. Off-screen until focused. */}
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar
        tab={tab}
        onSelectTab={openView}
        moreOpen={moreOpen}
        onToggleMore={toggleMore}
        onSelectPath={(path, candidate) => {
          selectCyclePath(...restoreNavigation(path, candidate));
          // Picking a campaign on a phone means "open it" — leave the list screen.
          // On a desktop nothing reads this.
          setListScreen(false);
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
          if (checkin) openView("chat");
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
          // On a phone the sidebar IS the list screen, so the reset it triggers
          // happens on the screen behind it — go there.
          setListScreen(false);
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
      {/* The active-run dock, floating on the sidebar's OUTER edge. A `.shell`
          child, not a sidebar one: the sidebar clips its overflow and Popover is
          not portaled. Desktop only — see JobsDock. */}
      <JobsDock onPicked={() => openView("dashboard")} />
      <main className="main" id="main-content" tabIndex={-1}>
        {/* Phone chrome. Hidden above --bp-md, where the sidebar carries all of
            this and there is no top bar at all. */}
        <MobileAppBar
          tab={tab}
          onSelectTab={openView}
          listScreen={listScreen}
          onBack={() => setListScreen(true)}
          onNewCycle={() => setNewCampaignOpen(true)}
        />
        {/* Loud failure surface — sticky, full-width, on every tab. Renders
            null on a healthy run; not gated on cycleId so a server-down state
            with no unit in view still screams. It stays mounted on the phone's
            list screen too: a dead address is exactly what you go to the list
            to fix. */}
        <CriticalAlertBanner
          bannerStatus={bannerStatus}
          bannerText={bannerText}
          bannerHint={bannerHint}
          onOpenFiles={() => openView("files")}
          onPauseCampaign={
            campaignId && cycleId
              ? () => void postPauseCycle(campaignId, cycleId)
              : undefined
          }
        />
        {showCheckin && campaignId ? (
          <CheckinReopenPane campaignId={campaignId} onStarted={selectAndOpen} />
        ) : tab === "chat" ? (
          <ChatPane
            datasetName={leafDatasetName}
            datasetItems={datasetItems}
            datasetMeasuredCount={datasetMeasuredCount}
            datasetUnmeasuredCount={datasetUnmeasuredCount}
            datasetSplitTest={datasetSplitTest}
            datasetOrder={datasetOrder}
            archivePerSample={archivePerSample}
            datasetTotals={datasetTotals}
            datasetStale={datasetStale}
            datasetError={datasetError}
            hardSamplesScope={hardSamplesScope}
            onHardSamplesScopeChange={setHardSamplesScope}
            hardSampleOrder={hardSampleOrder}
            onHardSampleOrderChange={setHardSampleOrder}
            newCampaignTick={newCampaignTick}
            onMinted={selectAndOpen}
          />
        ) : tab === "dashboard" ? (
          <DashboardTab />
        ) : tab === "files" ? (
          <FilesPane campaignId={campaignId} cycleId={cycleId} />
        ) : (
          <VerifyPane />
        )}
      </main>
      {/* Global remote — a bottom-fixed hovering strip on every tab, and the ONE surface
          answering "what is this run doing and costing": cycle picker, play/pause/skip,
          round/spend, babysat tag, the Lift/ETA/Δ-per-$ readout, and an upward panel with
          identity, spend and the finishing criteria. It deliberately survives `terminal` and
          `detached` — that is where the restart control and the outcome numbers matter — and
          renders null only for check-in and a cycle with no phase yet. Following the active
          run lands on its dashboard, same as the sidebar's jobs dock. */}
      <RemoteControl
        onFollowed={() => openView("dashboard")}
        cycleStartedAt={leafCreatedAt ?? cycleStartedAt}
      />
      {/* Mounted only while open so its chunk (+ ingest wizard deps) stays off
          first paint — IngestPane already hard-returns null when closed, so
          gating the mount is behaviour-identical. */}
      {newCampaignOpen && (
        <IngestPane
          open
          onClose={() => setNewCampaignOpen(false)}
          // start-checkin returns the (campaign, cycle) — select it now rather
          // than waiting on the 2 s workspace poll.
          onMinted={selectAndOpen}
        />
      )}
    </div>
    </ConnectorProvider>
    </LineageProvider>
    </SelectionProvider>
  );
}


