"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  fetchActive,
  fetchCampaigns,
  fetchCycles,
  type CampaignSummary,
  type CycleListEntry,
  type LifecycleFilter,
} from "./api";
import {
  encodeCyclePath,
  pathLeaf,
  pathRoot,
  type CyclePath,
} from "./ids";
import {
  EMPTY_ADDRESS,
  formatAddress,
  parseAddress,
  type Address,
  type CellAddress,
} from "./address";
import {
  DEFAULT_ACCOUNT_PANE,
  DEFAULT_TAB,
  type AccountPane,
  type Tab,
} from "./view-tab";
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation, useRevalidation } from "./revalidate";
import { useAuthGate } from "./auth-context";
import { isSelfOptimization } from "./derivations";
import { hasLiveProducer, dockPriority } from "./run-phase";

interface WorkspaceState {
  sessionId: string | null;
  activeCycleId: string | null;
  activeCampaignId: string | null;
  viewedPath: CyclePath | null;
  cycleId: string | null;
  campaignId: string | null;
  leafCampaignId: string | null;
  leafCycleId: string | null;
  // Read off the LEAF's campaign: a fork of a pp-self campaign is still self-optimizing, while an
  // inner run lives in a sandbox, is absent from `campaigns`, and correctly reads false.
  leafIsL4: boolean;
  // An ID, never a LABEL: `C1.1` is a course's private position and addresses nothing across a
  // campaign. NAVIGATION, written only by the tree — not `SelectionContext.candidate`.
  viewedCandidateId: string | null;
  datasetName: string | null;
  following: boolean;
  tab: Tab;
  setTab: (t: Tab) => void;
  // Several measurement panes can be on screen at once, so the cell names the pane that OWNS it;
  // null is the address's own, answered by the pane that claims the address.
  openCell: CellAddress | null;
  openCellOwner: string | null;
  setOpenCell: (c: CellAddress | null, owner?: string | null) => void;
  releaseCell: (owner: string) => void;
  accountPane: AccountPane | null;
  openAccount: (pane?: AccountPane) => void;
  closeAccount: () => void;
  cycles: CycleListEntry[];
  cyclesLoaded: boolean;
  // False from a filter change until the refetch for the new filter lands.
  campaignsLoaded: boolean;
  cyclesError: string | null;
  // Membership AND order derived once, so no "what's running" surface re-sorts its own copy (I6).
  runningCycles: CycleListEntry[];
  campaigns: CampaignSummary[];
  activeError: string | null;
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  selectCyclePath: (path: CyclePath, candidateId?: string | null) => void;
  // Both ids required — a cycle_id alone is ambiguous across campaigns.
  selectCycle: (campaignId: string, cycleId: string) => void;
  // One hop into the VIEWED leaf's sandbox; callers name a run and never build the path.
  drillInto: (campaignId: string, cycleId: string) => void;
  backToOuter: () => void;
  followActive: () => void;
  // Only the address's OWN read may report it, never list membership: an inner hop is absent from
  // `/cycles` and an archived campaign from the `active` filter.
  reportAddressGone: (address: string) => void;
  goneAddress: string | null;
  dismissGoneNotice: () => void;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspace(): WorkspaceState {
  const v = useContext(WorkspaceContext);
  if (!v) {
    throw new Error("useWorkspace must be called inside <WorkspaceProvider>");
  }
  return v;
}

// Matches `poll.tsx::RECONNECT_INTERVAL_MS`, so both polls retry a downed server on one beat.
const RECONNECT_INTERVAL_MS = 5000;

function urlAddress(): Address | null {
  if (typeof window === "undefined") return null;
  return parseAddress(window.location.hash);
}

const REGISTRY_INTERVAL_MS = 10000;
// Matches the dashboard's live beat, so a CLI-minted cycle is followed without the registry's lag.
const POINTER_INTERVAL_MS = 2000;

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [pinnedPath, setPinnedPath] = useState<CyclePath | null>(null);
  // Every write below sets it with `pinnedPath`, so a candidate never outlives its course.
  const [viewedCandidateId, setViewedCandidateId] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  // On the ADDRESS, whose one writer is this file; `AppShell::openView` is the one call site.
  const [tab, setTab] = useState<Tab>(DEFAULT_TAB);
  const [cellState, setCellState] = useState<{
    cell: CellAddress;
    owner: string | null;
  } | null>(null);
  const openCell = cellState?.cell ?? null;
  const openCellOwner = cellState?.owner ?? null;
  // The pin is deliberately NOT cleared while the modal is up, so closing returns to what was under.
  const [accountPane, setAccountPane] = useState<AccountPane | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null);
  const [cycles, setCycles] = useState<CycleListEntry[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([]);
  const [campaignsFilter, setCampaignsFilter] = useState<LifecycleFilter | null>(
    null,
  );
  const [cyclesLoaded, setCyclesLoaded] = useState(false);
  const [cyclesError, setCyclesError] = useState<string | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);
  const [lifecycleFilter, setLifecycleFilter] =
    useState<LifecycleFilter>("active");
  const [goneAddress, setGoneAddress] = useState<string | null>(null);

  const { authed, onAuthError } = useAuthGate();

  // The pointer is the tenant's LATEST launch, not the live set — several runs share a tenant, and
  // every one of them is a `/cycles` row carrying its own served `run_phase`.
  const prevActivePointerRef = useRef<string | null>(null);
  // Read by the pointer tick and `reportAddressGone`: `usePoll` restarts its loop when a tick's
  // identity changes, so neither may close over the pin itself.
  const pinnedRef = useRef<CyclePath | null>(pinnedPath);
  useEffect(() => {
    pinnedRef.current = pinnedPath;
  });

  // A null parse is a malformed hash and changes nothing, rather than a typo throwing the
  // operator back to the active run.
  const adoptAddress = useCallback((a: Address | null) => {
    if (!a) return;
    if (a.kind === "account") {
      setAccountPane(a.pane);
      return;
    }
    setAccountPane(null);
    setTab(a.tab);
    setCellState(a.cell ? { cell: a.cell, owner: null } : null);
    if (a.kind === "follow") {
      setFollowing(true);
      setPinnedPath(null);
      setViewedCandidateId(null);
      return;
    }
    setPinnedPath(a.path);
    setViewedCandidateId(a.candidateId);
    setFollowing(false);
  }, []);

  // Read in a mount effect, not a useState initializer, so the static-export HTML and the first
  // client render agree.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    adoptAddress(urlAddress());
    setInitialized(true);
  }, [adoptAddress]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // The writer below no-ops when the hash already matches, so this cannot loop against it.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onHash = () => adoptAddress(parseAddress(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [adoptAddress]);

  const reval = useRevalidation();

  const pointerTick = useCallback(
    async (signal: AbortSignal) => {
      let active;
      try {
        active = await fetchActive(signal);
      } catch (err) {
        if (signal.aborted) return;
        onAuthError(err);
        // "No active session" is null ids on a 200 (`active.py::get_active_session`), never here.
        setActiveError((err as Error)?.message ?? "active session unavailable");
        return;
      }
      if (signal.aborted) return;
      setSessionId(active.session_id || null);
      const nextActiveCycle = active.cycle_id || null;
      const nextActiveCampaign = active.campaign_id || null;
      setActiveCycleId(nextActiveCycle);
      setActiveCampaignId(nextActiveCampaign);
      setActiveError(null);
      // The first poll only sets the baseline. A pin moves only onto a new cycle of its OWN campaign
      // (a fork of what is on screen); a launch of any other run leaves the operator where they are.
      if (nextActiveCycle && nextActiveCampaign) {
        const nextPointer = `${nextActiveCampaign}::${nextActiveCycle}`;
        const prevPointer = prevActivePointerRef.current;
        const pinned = pinnedRef.current ? pathRoot(pinnedRef.current) : null;
        const forkOfPinned =
          pinned !== null &&
          pinned.campaignId === nextActiveCampaign &&
          pinned.cycleId !== nextActiveCycle;
        if (prevPointer !== null && prevPointer !== nextPointer && forkOfPinned) {
          setFollowing(true);
          setPinnedPath(null);
          setViewedCandidateId(null);
        }
        prevActivePointerRef.current = nextPointer;
      }
    },
    [onAuthError],
  );

  const registryTick = useCallback(
    async (signal: AbortSignal) => {
      const [cyclesRes, campaignsRes] = await Promise.allSettled([
        fetchCycles(signal),
        fetchCampaigns(undefined, signal, lifecycleFilter),
      ]);
      if (signal.aborted) return;
      for (const r of [cyclesRes, campaignsRes]) {
        if (r.status === "rejected") onAuthError(r.reason);
      }
      if (cyclesRes.status === "fulfilled") {
        setCycles(cyclesRes.value.cycles);
        setCyclesError(null);
      } else {
        setCyclesError(
          (cyclesRes.reason as Error)?.message ?? "campaign list unavailable",
        );
      }
      if (campaignsRes.status === "fulfilled") {
        setCampaigns(campaignsRes.value.campaigns);
        setCampaignsFilter(lifecycleFilter);
      }
      setCyclesLoaded(true);
    },
    [lifecycleFilter, onAuthError],
  );

  // Either read succeeding proves the API reachable.
  const wsOffline = activeError != null && cyclesError != null;
  usePoll(pointerTick, {
    intervalMs: wsOffline ? RECONNECT_INTERVAL_MS : POINTER_INTERVAL_MS,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: reval,
  });
  usePoll(registryTick, {
    intervalMs: wsOffline ? RECONNECT_INTERVAL_MS : REGISTRY_INTERVAL_MS,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: reval,
  });

  // Memoized: consumers key polls, memos and chart `options` on it, so a fresh array per render
  // forces a `chart.update()` on every pointer tick.
  const viewedPath: CyclePath | null = useMemo(
    () =>
      following
        ? activeCampaignId && activeCycleId
          ? [{ campaignId: activeCampaignId, cycleId: activeCycleId }]
          : null
        : pinnedPath,
    [following, activeCampaignId, activeCycleId, pinnedPath],
  );

  const rootHop = viewedPath ? pathRoot(viewedPath) : null;
  const campaignId = rootHop?.campaignId ?? null;
  const cycleId = rootHop?.cycleId ?? null;

  const leafHop = viewedPath ? pathLeaf(viewedPath) : null;
  const leafCampaignId = leafHop?.campaignId ?? null;
  const leafCycleId = leafHop?.cycleId ?? null;
  const leafIsL4 = isSelfOptimization(
    campaigns.find((c) => c.campaign_id === leafCampaignId)?.backend_type,
  );

  const cycleEntry =
    cycleId && campaignId
      ? (cycles.find(
          (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
        ) ?? null)
      : null;
  const datasetName = cycleEntry?.dataset_name ?? null;

  // A PAUSED cycle is absent: nothing drives it, and it stays reachable as a sidebar row.
  const runningCycles = useMemo(
    () =>
      cycles
        .filter((c) => hasLiveProducer(c.run_phase))
        .sort((a, b) => dockPriority(a.run_phase) - dockPriority(b.run_phase)),
    [cycles],
  );

  // The sole writer of the hash. `replaceState`, not `push`: Back leaves the app, as a dashboard's
  // should.
  useEffect(() => {
    if (!initialized || typeof window === "undefined") return;
    const want = formatAddress(
      accountPane != null
        ? { kind: "account", pane: accountPane }
        : following || !pinnedPath
          ? { kind: "follow", tab, cell: openCell }
          : { kind: "cycle", path: pinnedPath, tab, candidateId: viewedCandidateId, cell: openCell },
    );
    // The default view carries no hash at all, never a bare `#/`.
    const bare = want === EMPTY_ADDRESS;
    const now = window.location.hash;
    // Also stops this racing the `hashchange` listener above.
    if (bare ? now === "" || now === EMPTY_ADDRESS : now === want) return;
    window.history.replaceState(
      null,
      "",
      bare ? window.location.pathname + window.location.search : want,
    );
  }, [initialized, following, pinnedPath, viewedCandidateId, tab, openCell, accountPane]);

  const openAccount = useCallback(
    (pane: AccountPane = DEFAULT_ACCOUNT_PANE) => setAccountPane(pane),
    [],
  );
  const closeAccount = useCallback(() => setAccountPane(null), []);

  const selectCyclePath = useCallback(
    (path: CyclePath, candidate: string | null = null) => {
      setFollowing(false);
      setPinnedPath(path);
      setViewedCandidateId(candidate);
      setCellState(null);
    },
    [],
  );

  const selectCycle = useCallback(
    (cid: string, cyid: string) =>
      selectCyclePath([{ campaignId: cid, cycleId: cyid }]),
    [selectCyclePath],
  );

  const drillInto = useCallback(
    (cid: string, cyid: string) => {
      if (!viewedPath) return;
      selectCyclePath([...viewedPath, { campaignId: cid, cycleId: cyid }]);
    },
    [viewedPath, selectCyclePath],
  );

  const backToOuter = useCallback(() => {
    setPinnedPath((prev) => (prev && prev.length > 1 ? prev.slice(0, 1) : prev));
    setViewedCandidateId(null);
  }, []);

  const followActive = useCallback(() => {
    setFollowing(true);
    setPinnedPath(null);
    setViewedCandidateId(null);
    setCellState(null);
  }, []);

  const setOpenCell = useCallback(
    (c: CellAddress | null, owner: string | null = null) =>
      setCellState(c ? { cell: c, owner } : null),
    [],
  );
  const releaseCell = useCallback(
    (owner: string) => setCellState((prev) => (prev && prev.owner === owner ? null : prev)),
    [],
  );
  // Another view has no pane to answer the cell, so it would ride the address unseen and
  // pop open on the next visit.
  const selectTab = useCallback(
    (t: Tab) => {
      if (t !== tab) setCellState(null);
      setTab(t);
    },
    [tab],
  );

  // The caller has already confirmed the verdict (`poll.tsx::GONE_CONFIRM_LIMIT`); this only
  // refuses a late report from an address the operator has since moved off.
  const reportAddressGone = useCallback((address: string) => {
    const pinned = pinnedRef.current;
    if (!pinned || encodeCyclePath(pinned) !== address) return;
    setPinnedPath(null);
    setViewedCandidateId(null);
    setFollowing(true);
    setGoneAddress(address);
  }, []);

  const dismissGoneNotice = useCallback(() => setGoneAddress(null), []);

  // Re-tick the registry now rather than after its 10 s interval.
  const selectLifecycle = useCallback((f: LifecycleFilter) => {
    setLifecycleFilter(f);
    bumpRevalidation();
  }, []);

  const value: WorkspaceState = {
    sessionId,
    activeCycleId,
    activeCampaignId,
    viewedPath,
    cycleId,
    campaignId,
    leafCampaignId,
    leafCycleId,
    leafIsL4,
    viewedCandidateId,
    datasetName,
    following,
    tab,
    setTab: selectTab,
    openCell,
    openCellOwner,
    setOpenCell,
    releaseCell,
    accountPane,
    openAccount,
    closeAccount,
    cycles,
    cyclesLoaded,
    campaignsLoaded: campaignsFilter === lifecycleFilter,
    cyclesError,
    runningCycles,
    campaigns,
    activeError,
    lifecycleFilter,
    setLifecycleFilter: selectLifecycle,
    selectCyclePath,
    selectCycle,
    drillInto,
    backToOuter,
    followActive,
    reportAddressGone,
    goneAddress,
    dismissGoneNotice,
  };
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
