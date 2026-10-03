"use client";
import {
  startTransition,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import dynamic from "next/dynamic";
import { subjectKey } from "@/lib/api";
import { postPauseCycle } from "@/lib/api/commands";
import { useCommand } from "@/lib/hooks/useCommand";
import { CycleStreamProvider } from "@/lib/poll";
import { ConnectorProvider } from "@/lib/hooks/useConnector";
import { useWorkspace } from "@/lib/workspace";
import { HardSamplesProvider } from "@/lib/hard-samples";
import { useLeafCycleIndex } from "@/lib/hooks/useLeafCycleIndex";
import { useLocalStorage } from "@/lib/hooks/useLocalStorage";
import { decodeCyclePath, encodeCyclePath, type CyclePath } from "@/lib/ids";
import { applyChartDefaults } from "@/lib/theme";
import { cx } from "@/lib/cx";
import type { Tab } from "@/lib/view-tab";
import { VendorSprite } from "@/components/ui";
import { AccountModal } from "@/components/account/AccountModal";
import { Sidebar } from "@/components/shell/Sidebar";
import { SidebarResizer } from "@/components/shell/SidebarResizer";
import { JobsDock } from "@/components/shell/JobsDock";
import { MobileAppBar } from "@/components/shell/MobileAppBar";
import { DashboardTab } from "@/components/dashboard/layout/DashboardTab";
import { ComparePane } from "@/components/compare/ComparePane";
import { MeasurementsPane } from "@/components/shell/measurements/MeasurementsPane";
import { CompareSelectionProvider } from "@/lib/compare-selection";
import { SelectionProvider } from "@/lib/SelectionContext";
import { LineageProvider } from "@/lib/lineage";
import { IngestFlowProvider, useIngest } from "@/lib/ingest-flow";
import { useViewMemory } from "@/lib/view-memory";
import { CriticalAlertBanner } from "@/components/shell/CriticalAlertBanner";
import { RemoteControl } from "@/components/shell/RemoteControl";
import { RunMasthead } from "@/components/shell/RunMasthead";

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
// The specifiers of the `dynamic` panes above, so intent on the tab strip warms their chunks.
// A miss resurfaces at the real load, which `ui/ErrorBoundary` owns.
function preloadLazyPanes(): void {
  for (const chunk of [
    import("@/components/chat/ChatPane"),
    import("@/components/tree/FilesPane"),
    import("@/components/verify/VerifyPane"),
  ]) {
    void chunk.catch(() => undefined);
  }
}

const GONE_NOTICE_MS = 8000;

const SIDEBAR_DEFAULT = 200;
const SIDEBAR_MIN = 160;
const SIDEBAR_MAX = 480;

export function AppShell() {
  const { viewedPath } = useWorkspace();
  return (
    // Outside the cycle-keyed providers: a comparison spans campaigns, and a cycle-scoped
    // holder would drop the first pick on navigating to the second.
    <CompareSelectionProvider>
      <CycleStreamProvider path={viewedPath}>
        {/* ONE authoring thread for every entry point; above the shell so the modal cannot
            hold a second one. */}
        <IngestFlowProvider>
          <AppShellInner />
        </IngestFlowProvider>
      </CycleStreamProvider>
    </CompareSelectionProvider>
  );
}

function AppShellInner() {
  const {
    viewedPath,
    campaignId,
    cycleId,
    datasetName,
    cycles,
    selectCyclePath,
    leafCycleId,
    viewedCandidateId,
    goneAddress,
    dismissGoneNotice,
    tab,
    setTab,
    accountPane,
    closeAccount,
  } = useWorkspace();

  const pause = useCommand<"pause-cycle">("critical-alert");

  const { viewFor, recordView } = useViewMemory();

  useEffect(() => {
    if (!campaignId || !viewedPath) return;
    recordView(campaignId, {
      viewedPath: encodeCyclePath(viewedPath),
      viewedCandidateId,
    });
  }, [campaignId, viewedPath, viewedCandidateId, recordView]);

  // Memory would restore the dead address on reload, so its record goes. Only NAVIGATION
  // clears: for a reaped `.inner/` leaf the root campaign is still alive.
  useEffect(() => {
    if (!goneAddress) return;
    const rootCampaign = decodeCyclePath(goneAddress)?.[0]?.campaignId;
    if (rootCampaign) recordView(rootCampaign, { viewedPath: null, viewedCandidateId: null });
    const t = window.setTimeout(dismissGoneNotice, GONE_NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [goneAddress, recordView, dismissGoneNotice]);

  // A click on a campaign's ROOT row means "where I left it"; any deeper click is an explicit
  // address, and memory never overrides a live intent.
  const restoreNavigation = useCallback(
    (path: CyclePath, candidate?: string | null): [CyclePath, string | null] => {
      if (path.length !== 1 || candidate) return [path, candidate ?? null];
      const hop = path[0]!;
      // Only a campaign SWITCH restores: the viewed campaign's root row is the escape hatch,
      // and restoring there would re-drill forever against the record effect above.
      if (hop.campaignId === campaignId) return [path, null];
      const mem = viewFor(hop.campaignId);
      const remembered = decodeCyclePath(mem.viewedPath ?? "");
      if (!remembered || remembered[0]?.campaignId !== hop.campaignId) return [path, null];
      if (encodeCyclePath(remembered) === encodeCyclePath(path)) return [path, null];
      // Inner hops never appear in `/cycles`, so the ROOT hop's existence is the check; a fork
      // deleted between visits must open at the root, not spin on a 404.
      const root = remembered[0]!;
      const known = cycles.some(
        (c) => c.campaign_id === root.campaignId && c.cycle_id === root.cycleId,
      );
      return known ? [remembered, mem.viewedCandidateId] : [path, null];
    },
    [campaignId, viewFor, cycles],
  );
  const { datasetName: leafDatasetName, createdAt: cycleStartedAt } = useLeafCycleIndex(
    viewedPath,
    datasetName,
    cycles,
  );
  // The sandbox chain is the hops ABOVE the leaf, so an L4 inner run resolves its OWN pipeline
  // rather than the outer campaign's (`frontend-surface-contract.md::I9`).
  const leafHop = viewedPath?.length ? viewedPath[viewedPath.length - 1] : null;
  const connectorAt = useMemo(
    () =>
      leafHop && viewedPath
        ? subjectKey("course", [leafHop.campaignId, leafHop.cycleId], viewedPath.slice(0, -1))
        : null,
    [leafHop, viewedPath],
  );
  // Which PHONE screen shows; `false` = the campaign screen, so an anon visitor lands on the
  // public surface rather than a sign-in list. Inert above --bp-md.
  const [listScreen, setListScreen] = useState(false);
  const [newCampaignOpen, setNewCampaignOpen] = useState(false);
  const { flow: ingestFlow, startNew, mintCount } = useIngest();
  const [sidebarCollapsed, setSidebarCollapsed] = useLocalStorage<boolean>(
    "promptpotter.sidebar.collapsed",
    false,
    { serialize: (v) => (v ? "1" : "0"), deserialize: (raw) => raw === "1" },
  );
  const toggleSidebar = useCallback(
    () => setSidebarCollapsed((prev) => !prev),
    [setSidebarCollapsed],
  );
  // Applied inline only while expanded, so the collapsed rail's class rule still wins.
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
  // The ONE place a view switches; leaving the phone's list screen rides here rather than at
  // every caller.
  const switchView = useCallback(
    (t: Tab) => {
      setTab(t);
      setListScreen(false);
    },
    [setTab],
  );
  // A transition, so the pane being left stays interactive while the next one renders.
  const openView = useCallback(
    (t: Tab) => startTransition(() => switchView(t)),
    [switchView],
  );

  // A mint lands the operator on what it created. Selecting the new cycle is the provider's
  // job; leaving the phone list screen is the shell's half.
  const [prevMintCount, setPrevMintCount] = useState(mintCount);
  if (mintCount !== prevMintCount) {
    setPrevMintCount(mintCount);
    setListScreen(false);
  }

  // The modal is the ENTRY to the thread: once the shared flow leaves `idle`, hand over to the
  // chat tab and close, in render phase so the modal never paints over the thread.
  const ingestStage = ingestFlow.phase.stage;
  const [prevIngestStage, setPrevIngestStage] = useState(ingestStage);
  if (ingestStage !== prevIngestStage) {
    setPrevIngestStage(ingestStage);
    if (newCampaignOpen && ingestStage !== "idle") {
      setNewCampaignOpen(false);
      switchView("chat");
    }
  }

  useEffect(() => {
    applyChartDefaults();
  }, []);

  const isCheckin = useCallback(
    (campaign: string, cycle: string | null) =>
      cycles.some(
        (c) => c.campaign_id === campaign && c.cycle_id === cycle && c.run_phase === "checkin",
      ),
    [cycles],
  );

  const selectedCheckin = !!campaignId && isCheckin(campaignId, cycleId);
  // Scoped to the chat tab, not every tab, so a selected check-in never traps navigation.
  const showCheckin = selectedCheckin && tab === "chat";

  // Stable identities: the sidebar memoizes its whole forest on `onSelectPath`.
  const onSelectPath = useCallback(
    (path: CyclePath, candidate?: string | null) => {
      selectCyclePath(...restoreNavigation(path, candidate));
      setListScreen(false);
      // Selecting never hijacks the tab, except a check-in (no dashboard.json) goes to Chat.
      // An inner run is never a check-in, so a descended path never redirects.
      if (path.length > 1) return;
      const hop = path[0]!;
      if (isCheckin(hop.campaignId, hop.cycleId)) openView("chat");
    },
    [selectCyclePath, restoreNavigation, isCheckin, openView],
  );
  const onNewCycle = useCallback(() => {
    // Two doors onto one thread: on the chat tab it resets in place; elsewhere the modal
    // opens and hands over once something is picked.
    if (tab === "chat") startNew();
    else setNewCampaignOpen(true);
    setListScreen(false);
  }, [tab, startNew]);
  const openFiles = useCallback(() => openView("files"), [openView]);
  const openDashboard = useCallback(() => openView("dashboard"), [openView]);

  return (
    <SelectionProvider cycleId={leafCycleId}>
    <ConnectorProvider campaignId={leafHop?.campaignId ?? null} at={connectorAt}>
    {/* ONE lineage fetch owner, rooted at the ROOT hop. Inside the providers above: it
        composes SelectionProvider's `sampleSet` into the masked read. */}
    <LineageProvider campaignId={campaignId} cycleId={cycleId}>
    {/* Here, not in the chat tab: its consumers sit on two different branches of that tab. */}
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
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <Sidebar
        onSelectPath={onSelectPath}
        onNewCycle={onNewCycle}
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
      {/* A `.shell` child, not a sidebar one: the sidebar clips its overflow. */}
      <JobsDock onPicked={openDashboard} />
      <main className="main" id="main-content" tabIndex={-1}>
        {/* The view axis is NOT here: ViewTabs owns it. */}
        <MobileAppBar
          listScreen={listScreen}
          onBack={() => setListScreen(true)}
          onNewCycle={() => setNewCampaignOpen(true)}
        />
        {/* Not gated on cycleId, so a server-down state with no unit in view still shows;
            mounted on the phone list screen too, where a dead address gets fixed. */}
        <CriticalAlertBanner
          onOpenFiles={openFiles}
          onPauseCampaign={
            campaignId && cycleId
              ? () =>
                  void pause.run("pause-cycle", () => postPauseCycle(campaignId, cycleId))
              : undefined
          }
        />
        {/* Chrome rather than a pane's first child, so it cannot scroll away. */}
        <RunMasthead
          tab={tab}
          onSelectTab={openView}
          onTabIntent={preloadLazyPanes}
          onFollowed={openDashboard}
        />
        {tab === "chat" ? (
          <ChatPane
            checkinCampaignId={showCheckin ? campaignId : null}
            onOpenDashboard={openDashboard}
          />
        ) : tab === "dashboard" ? (
          <DashboardTab />
        ) : tab === "measurements" ? (
          <MeasurementsPane claimsAddress />
        ) : tab === "compare" ? (
          <ComparePane />
        ) : tab === "files" ? (
          <FilesPane campaignId={campaignId} cycleId={cycleId} />
        ) : (
          <VerifyPane />
        )}
      </main>
      <RemoteControl cycleStartedAt={cycleStartedAt} />
      {/* Mounted only while open so its chunk stays off first paint. */}
      {newCampaignOpen && <IngestPane open onClose={() => setNewCampaignOpen(false)} />}
      {/* A `.shell` child, not a sidebar one: the phone hides the sidebar off its list
          screen, and a deep link to `#/account/<pane>` must open wherever it lands. */}
      <AccountModal open={accountPane != null} onClose={closeAccount} />
      {/* Mounted ONCE: every `VendorLogo` is a `<use href="#id">` into it, so unmounting it
          blanks every mark on every surface. */}
      <VendorSprite />
    </div>
    </HardSamplesProvider>
    </LineageProvider>
    </ConnectorProvider>
    </SelectionProvider>
  );
}


