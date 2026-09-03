"use client";
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import dynamic from "next/dynamic";
import { fetchCycleFile } from "@/lib/api";
import { postPauseCycle } from "@/lib/api/commands";
import { CycleStreamProvider } from "@/lib/poll";
import { ConnectorProvider } from "@/lib/hooks/useConnector";
import { useDashboard } from "@/lib/hooks/useDashboard";
import { useWorkspace } from "@/lib/workspace";
import { HardSamplesProvider } from "@/lib/hard-samples";
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
import { ComparePane } from "@/components/compare/ComparePane";
import { CompareSelectionProvider } from "@/lib/compare-selection";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageProvider } from "@/lib/lineage";
import { IngestFlowProvider, useIngest } from "@/lib/ingest-flow";
import { useViewMemory } from "@/lib/view-memory";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteControl } from "@/components/shell/RemoteControl";
import { RunMasthead } from "@/components/shell/RunMasthead";

// The non-landing surfaces load on demand, not on first paint. The operator
// lands on the dashboard tab; Chat / Files / Verify (and the markdown renderer
// `marked` that rides inside Files) and the Ingest modal each ship as their own
// chunk fetched only when the view is opened — `ssr: false` since they're
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
    // The compare selection sits OUTSIDE both the stream and `SelectionProvider`: those are
    // keyed on the viewed cycle and reset when it changes, while a comparison spans campaigns by
    // construction — picking a searchpoint, navigating to another run and picking a second is the
    // whole point, and a cycle-scoped holder would drop the first on the way.
    <CompareSelectionProvider>
      <CycleStreamProvider path={viewedPath}>
        {/* ONE authoring thread for every entry point — the chat tab, the New
            campaign modal and a re-opened check-in all drive the same draft.
            Above the shell so the modal cannot hold a second one. */}
        <IngestFlowProvider>
          <AppShellInner />
        </IngestFlowProvider>
      </CycleStreamProvider>
    </CompareSelectionProvider>
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
    selectCyclePath,
    leafCycleId,
    viewedCandidateId,
    goneAddress,
    dismissGoneNotice,
    tab,
    setTab,
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

  // RESTORE. A click on a campaign's ROOT row means "where I left it"; any deeper click is an
  // explicit address, and memory never overrides a live intent. Guarded on the remembered leaf
  // still being a cycle the workspace knows, or a deleted fork spins on an address that 404s.
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
  // The DISPLAY panes follow the VIEWED LEAF hop, so drilling into an L4 inner loop shows that
  // run's connector and samples; the CHAT THREAD stays on the root hop below. At depth 1 the two
  // coincide, so the top-level view is unchanged.
  const { datasetName: leafDatasetName, createdAt: leafCreatedAt } = useLeafCycleIndex(
    viewedPath,
    datasetName,
  );
  // The per-campaign view is ON THE ADDRESS (`lib/address.ts`), so the workspace holds it and a
  // reload or copied link restores the pane rather than dropping the operator on Chat.
  // Which of the two PHONE screens is showing. `false` = the campaign screen, the public landing
  // surface: an anon visitor booted onto "Sign in to see your campaigns" never reaches what they
  // came for. Inert above --bp-md — no desktop rule reads the class.
  const [listScreen, setListScreen] = useState(false);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  // The one authoring thread. The modal and the chat tab are two doors onto it,
  // never two of it.
  const { flow: ingestFlow, startNew, mintCount } = useIngest();
  const [cycleStartedAt, setCycleStartedAt] = useState<string | null>(null);
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
  // The ONE call site that switches views. Choosing a view means "show me this
  // campaign", so leaving the phone's list screen rides along here rather than at every
  // caller — which is also why no render-phase reset is needed: the invariant is
  // structural, leaving no derived state to correct. The view itself is the workspace's
  // (it is on the address); `listScreen` is this component's, being phone chrome.
  const openView = useCallback(
    (t: Tab) => {
      setTab(t);
      setListScreen(false);
    },
    [setTab],
  );

  // A mint lands the operator ON the thing it just created, which on a phone
  // means leaving the list screen. Selecting the new cycle is the provider's
  // job (every entry point shares it); this is the shell's own half, guarded in
  // render phase so it commits with the same frame.
  const [prevMintCount, setPrevMintCount] = useState(mintCount);
  if (mintCount !== prevMintCount) {
    setPrevMintCount(mintCount);
    setListScreen(false);
  }

  // The modal is the ENTRY to the thread, not a second copy of it: the moment a
  // pick or a drop advances the shared flow past `idle`, hand it to the chat tab
  // — the surface that can actually hold a conversation — and close. Guarded on
  // the stage EDGE in render phase, so the handover commits with the frame that
  // advanced it and the modal never paints over the thread it just started.
  const ingestStage = ingestFlow.phase.stage;
  const [prevIngestStage, setPrevIngestStage] = useState(ingestStage);
  if (ingestStage !== prevIngestStage) {
    setPrevIngestStage(ingestStage);
    if (newCampaignOpen && ingestStage !== "idle") {
      setNewCampaignOpen(false);
      openView("chat");
    }
  }

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
  // down server also reports zero cycles, so netDown is subtracted below.
  const noUnit = !cycleId;
  const netDown = Boolean(activeError || cyclesError);
  // Nothing has ever run here, and the server is fine. Passed to the banner as
  // its own fact rather than dressed up as a status: the poll's resting state is
  // already `offline`, so anything short of an explicit signal paints a fresh
  // account the same red as an outage. What to do about it is the sidebar's
  // empty state, which points at the `+ New campaign` button already on screen.
  const emptyWorkspace = noUnit && cyclesLoaded && !netDown && cycles.length === 0;
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
    {/* The roster for the unit in view + the scope and ranking that pick it. Here
        rather than in the chat tab because its consumers sit on two different
        branches of that tab — the hero's heat-map and the run card's table. */}
    <HardSamplesProvider path={viewedPath} datasetName={leafDatasetName}>
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
          // Two doors onto one thread, picked by the view in front of the operator: already on
          // the chat tab, the thread resets in place; anywhere else the modal opens as the
          // entry list and hands over as soon as something is picked. A check-in no longer
          // needs an exception — it is a stage of the chat surface now, not a takeover.
          if (tab === "chat") startNew();
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
        {/* Phone chrome — the back arrow to the list screen and the campaign's
            verbs. Hidden above --bp-md, where the sidebar carries both. The view
            axis is NOT here: ViewTabs owns it, at the foot of the screen. */}
        <MobileAppBar
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
          emptyWorkspace={emptyWorkspace}
          onOpenFiles={() => openView("files")}
          onPauseCampaign={
            campaignId && cycleId
              ? () => void postPauseCycle(campaignId, cycleId)
              : undefined
          }
        />
        {/* The unit header — what am I looking at, and which view of it. Chrome
            rather than a pane's first child, so the strip cannot scroll away. */}
        <RunMasthead tab={tab} onSelectTab={openView} />
        {tab === "chat" ? (
          <ChatPane
            // A durable check-in has no dashboard.json, so it is authored rather than
            // watched. It used to swap in a whole second pane for that — losing the
            // hero, the pipeline and the samples on the way — when it is really one
            // stage of this surface: hand the campaign over and let the thread reopen it.
            checkinCampaignId={showCheckin ? campaignId : null}
          />
        ) : tab === "dashboard" ? (
          <DashboardTab />
        ) : tab === "compare" ? (
          <ComparePane />
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
      {newCampaignOpen && <IngestPane open onClose={() => setNewCampaignOpen(false)} />}
    </div>
    </HardSamplesProvider>
    </ConnectorProvider>
    </LineageProvider>
    </SelectionProvider>
  );
}


