"use client";

import {
  createContext,
  startTransition,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  decodeCyclePath,
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
  isWorkspaceTab,
  type AccountPane,
  type CampaignTab,
  type Tab,
} from "./view-tab";
import { useActivePointer, useCampaign, useCycleEntry, useRegistry } from "./registry";
import { phaseIs } from "./run-phase";
import { useViewMemory } from "./view-memory";

export interface NavigateOptions {
  candidate?: string | null;
  // A campaign's ROOT row only: view memory may deepen the path.
  resume?: boolean;
  view?: Tab;
}

interface WorkspaceState {
  viewedPath: CyclePath | null;
  cycleId: string | null;
  campaignId: string | null;
  leafCampaignId: string | null;
  leafCycleId: string | null;
  viewedCandidateId: string | null;
  following: boolean;
  tab: Tab;
  // An offset in the viewed LEAF's ledger (`RayItem.offset`); null is the head.
  at: number | null;
  setAt: (offset: number | null) => void;
  openCell: CellAddress | null;
  // The pane that owns the open cell; null is the address's own.
  openCellOwner: string | null;
  setOpenCell: (c: CellAddress | null, owner?: string | null) => void;
  releaseCell: (owner: string) => void;
  accountPane: AccountPane | null;
  openAccount: (pane?: AccountPane) => void;
  closeAccount: () => void;
  // Which PHONE screen shows; inert above --bp-md.
  listScreen: boolean;
  showList: () => void;
  navigate: (path: CyclePath, opts?: NavigateOptions) => void;
  drillInto: (campaignId: string, cycleId: string) => void;
  backToOuter: () => void;
  followActive: (view?: Tab) => void;
  openView: (tab: Tab) => void;
  backToCampaign: () => void;
  reportAddressGone: (address: string) => void;
  goneAddress: string | null;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspace(): WorkspaceState {
  const v = useContext(WorkspaceContext);
  if (!v) {
    throw new Error("useWorkspace must be called inside <WorkspaceProvider>");
  }
  return v;
}

export function useMomentAt(path: CyclePath | null): number | null {
  const { at, viewedPath } = useWorkspace();
  if (at === null || path === null || viewedPath === null) return null;
  return encodeCyclePath(path) === encodeCyclePath(viewedPath) ? at : null;
}

// An inner run is absent from `campaigns` and correctly reads false.
export function useLeafIsL4(): boolean {
  return useCampaign(useWorkspace().leafCampaignId)?.self_optimization === true;
}

// The ROOT hop's dataset; a drilled-in leaf's is `useLeafDatasetName`.
export function useViewedDatasetName(): string | null {
  const { campaignId, cycleId } = useWorkspace();
  return useCycleEntry(campaignId, cycleId)?.dataset_name ?? null;
}

function urlAddress(): Address | null {
  if (typeof window === "undefined") return null;
  return parseAddress(window.location.hash);
}

const GONE_NOTICE_MS = 8000;

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [pinnedPath, setPinnedPath] = useState<CyclePath | null>(null);
  // Every write below sets it with `pinnedPath`, so a candidate never outlives its course.
  const [viewedCandidateId, setViewedCandidateId] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  const [tab, setTab] = useState<Tab>(DEFAULT_TAB);
  const [moment, setMoment] = useState<{ address: string; offset: number } | null>(null);
  const [cellState, setCellState] = useState<{
    cell: CellAddress;
    owner: string | null;
  } | null>(null);
  const openCell = cellState?.cell ?? null;
  const openCellOwner = cellState?.owner ?? null;
  // The pin is deliberately NOT cleared while the modal is up, so closing returns to what was under.
  const [accountPane, setAccountPane] = useState<AccountPane | null>(null);
  // `false` = the campaign screen, so an anon visitor lands on the public surface, not a list.
  const [listScreen, setListScreen] = useState(false);
  const [initialized, setInitialized] = useState(false);
  const [goneAddress, setGoneAddress] = useState<string | null>(null);

  const { cycles } = useRegistry();
  const active = useActivePointer();
  const { viewFor, recordView } = useViewMemory();

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
      setMoment(null);
      return;
    }
    setPinnedPath(a.path);
    setViewedCandidateId(a.candidateId);
    setMoment(a.at === null ? null : { address: encodeCyclePath(a.path), offset: a.at });
    setFollowing(false);
  }, []);

  // A mount effect, not a useState initializer: the static-export HTML and first client render must agree.
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

  // A pin moves only onto a fork of its OWN campaign; the first pointer only sets the baseline.
  const pointer =
    active.campaignId && active.cycleId ? `${active.campaignId}::${active.cycleId}` : null;
  const [prevPointer, setPrevPointer] = useState<string | null>(null);
  if (pointer !== null && pointer !== prevPointer) {
    setPrevPointer(pointer);
    const pinned = pinnedPath ? pathRoot(pinnedPath) : null;
    const forkOfPinned =
      pinned !== null &&
      pinned.campaignId === active.campaignId &&
      pinned.cycleId !== active.cycleId;
    if (prevPointer !== null && forkOfPinned) {
      setFollowing(true);
      setPinnedPath(null);
      setViewedCandidateId(null);
    }
  }

  // Memoized: consumers key reads and chart `options` on its identity.
  const viewedPath: CyclePath | null = useMemo(
    () =>
      following
        ? active.campaignId && active.cycleId
          ? [{ campaignId: active.campaignId, cycleId: active.cycleId }]
          : null
        : pinnedPath,
    [following, active.campaignId, active.cycleId, pinnedPath],
  );

  const rootHop = viewedPath ? pathRoot(viewedPath) : null;
  const campaignId = rootHop?.campaignId ?? null;
  const cycleId = rootHop?.cycleId ?? null;

  const leafHop = viewedPath ? pathLeaf(viewedPath) : null;
  const leafCampaignId = leafHop?.campaignId ?? null;
  const leafCycleId = leafHop?.cycleId ?? null;

  const viewedAddress = viewedPath ? encodeCyclePath(viewedPath) : null;
  const at = moment !== null && moment.address === viewedAddress ? moment.offset : null;

  // `replaceState`, not `push`: Back leaves the app.
  useEffect(() => {
    if (!initialized || typeof window === "undefined") return;
    const want = formatAddress(
      accountPane != null
        ? { kind: "account", pane: accountPane }
        : following || !pinnedPath
          ? { kind: "follow", tab, cell: openCell }
          : {
              kind: "cycle",
              path: pinnedPath,
              tab,
              candidateId: viewedCandidateId,
              at,
              cell: openCell,
            },
    );
    // The default view carries no hash at all, never a bare `#/`.
    const bare = want === EMPTY_ADDRESS;
    const now = window.location.hash;
    if (bare ? now === "" || now === EMPTY_ADDRESS : now === want) return;
    window.history.replaceState(
      null,
      "",
      bare ? window.location.pathname + window.location.search : want,
    );
  }, [initialized, following, pinnedPath, viewedCandidateId, tab, at, openCell, accountPane]);

  useEffect(() => {
    if (!campaignId || !viewedAddress) return;
    recordView(campaignId, { viewedPath: viewedAddress, viewedCandidateId });
  }, [campaignId, viewedAddress, viewedCandidateId, recordView]);

  const openAccount = useCallback(
    (pane: AccountPane = DEFAULT_ACCOUNT_PANE) => setAccountPane(pane),
    [],
  );
  const closeAccount = useCallback(() => setAccountPane(null), []);

  // Another view has no pane to answer the cell: kept, it pops open on the next visit.
  const tabRef = useRef(tab);
  useEffect(() => {
    tabRef.current = tab;
  });
  const openView = useCallback(
    (t: Tab) =>
      startTransition(() => {
        if (t !== tabRef.current) setCellState(null);
        setTab(t);
        setListScreen(false);
      }),
    [],
  );

  // Render-phase, because the hash can move `tab` too.
  const [campaignTab, setCampaignTab] = useState<CampaignTab>(
    isWorkspaceTab(tab) ? DEFAULT_TAB : tab,
  );
  if (!isWorkspaceTab(tab) && tab !== campaignTab) setCampaignTab(tab);
  const backToCampaign = useCallback(() => openView(campaignTab), [openView, campaignTab]);

  const isCheckin = useCallback(
    (hop: { campaignId: string; cycleId: string }) =>
      cycles.some(
        (c) =>
          c.campaign_id === hop.campaignId &&
          c.cycle_id === hop.cycleId &&
          phaseIs(c.run_phase, "authoring"),
      ),
    [cycles],
  );

  const resumed = useCallback(
    (path: CyclePath): [CyclePath, string | null] => {
      const hop = path[0];
      // Only a campaign SWITCH resumes: on the viewed campaign it re-drills forever against the record effect.
      if (path.length !== 1 || !hop || hop.campaignId === campaignId) return [path, null];
      const mem = viewFor(hop.campaignId);
      const remembered = decodeCyclePath(mem.viewedPath ?? "");
      const root = remembered?.[0];
      if (!remembered || !root || root.campaignId !== hop.campaignId) return [path, null];
      if (encodeCyclePath(remembered) === encodeCyclePath(path)) return [path, null];
      // Inner hops never appear in `/cycles`, so the ROOT hop's existence is the check.
      const known = cycles.some(
        (c) => c.campaign_id === root.campaignId && c.cycle_id === root.cycleId,
      );
      return known ? [remembered, mem.viewedCandidateId] : [path, null];
    },
    [campaignId, viewFor, cycles],
  );

  const navigate = useCallback(
    (path: CyclePath, opts: NavigateOptions = {}) => {
      const root = path[0];
      if (!root) return;
      const [to, candidate] =
        opts.resume && !opts.candidate ? resumed(path) : [path, opts.candidate ?? null];
      setFollowing(false);
      setPinnedPath(to);
      setViewedCandidateId(candidate);
      setMoment(null);
      setCellState(null);
      setListScreen(false);
      // An inner run is never a check-in, so a descended path never redirects to Chat.
      if (opts.view) openView(opts.view);
      else if (path.length === 1 && isCheckin(root)) openView("chat");
      else if (isWorkspaceTab(tab)) openView(campaignTab);
    },
    [resumed, isCheckin, openView, tab, campaignTab],
  );

  const drillInto = useCallback(
    (cid: string, cyid: string) => {
      if (viewedPath) navigate([...viewedPath, { campaignId: cid, cycleId: cyid }]);
    },
    [viewedPath, navigate],
  );

  const backToOuter = useCallback(() => {
    setPinnedPath((prev) => (prev && prev.length > 1 ? prev.slice(0, 1) : prev));
    setViewedCandidateId(null);
    setMoment(null);
  }, []);

  const followActive = useCallback(
    (view?: Tab) => {
      setFollowing(true);
      setPinnedPath(null);
      setViewedCandidateId(null);
      setMoment(null);
      setCellState(null);
      if (view) openView(view);
    },
    [openView],
  );

  const showList = useCallback(() => setListScreen(true), []);

  const setAt = useCallback(
    (offset: number | null) =>
      setMoment(
        offset === null || viewedAddress === null ? null : { address: viewedAddress, offset },
      ),
    [viewedAddress],
  );

  const setOpenCell = useCallback(
    (c: CellAddress | null, owner: string | null = null) =>
      setCellState(c ? { cell: c, owner } : null),
    [],
  );
  const releaseCell = useCallback(
    (owner: string) => setCellState((prev) => (prev && prev.owner === owner ? null : prev)),
    [],
  );

  // Read by `reportAddressGone`, whose identity a polled read holds across renders.
  const pinnedRef = useRef<CyclePath | null>(pinnedPath);
  useEffect(() => {
    pinnedRef.current = pinnedPath;
  });

  // The caller has already confirmed the verdict (`useRead.ts::GONE_CONFIRM_LIMIT`).
  const reportAddressGone = useCallback(
    (address: string) => {
      const pinned = pinnedRef.current;
      if (!pinned || encodeCyclePath(pinned) !== address) return;
      // Memory would restore the dead address on reload; a reaped `.inner/` leaf's root campaign is still alive.
      recordView(pathRoot(pinned).campaignId, { viewedPath: null, viewedCandidateId: null });
      setPinnedPath(null);
      setViewedCandidateId(null);
      setFollowing(true);
      setGoneAddress(address);
    },
    [recordView],
  );

  useEffect(() => {
    if (!goneAddress) return;
    const t = window.setTimeout(() => setGoneAddress(null), GONE_NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [goneAddress]);

  const value = useMemo<WorkspaceState>(
    () => ({
      viewedPath,
      cycleId,
      campaignId,
      leafCampaignId,
      leafCycleId,
      viewedCandidateId,
      following,
      tab,
      at,
      setAt,
      openCell,
      openCellOwner,
      setOpenCell,
      releaseCell,
      accountPane,
      openAccount,
      closeAccount,
      listScreen,
      showList,
      navigate,
      drillInto,
      backToOuter,
      followActive,
      openView,
      backToCampaign,
      reportAddressGone,
      goneAddress,
    }),
    [
      viewedPath,
      cycleId,
      campaignId,
      leafCampaignId,
      leafCycleId,
      viewedCandidateId,
      following,
      tab,
      at,
      setAt,
      openCell,
      openCellOwner,
      setOpenCell,
      releaseCell,
      accountPane,
      openAccount,
      closeAccount,
      listScreen,
      showList,
      navigate,
      drillInto,
      backToOuter,
      followActive,
      openView,
      backToCampaign,
      reportAddressGone,
      goneAddress,
    ],
  );
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
