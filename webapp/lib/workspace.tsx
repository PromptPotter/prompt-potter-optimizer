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
  DEFAULT_VIEWS,
  EMPTY_ADDRESS,
  formatAddress,
  nodeOfKey,
  parseAddress,
  withViews,
  type Address,
  type FamilyId,
  type SelectedNode,
  type View,
  type ViewedSubject,
} from "./address";
import type { ListedCampaign } from "@/lib/api/types";
import { GONE_NOTICE_MS } from "./cadence";
import { tagValue } from "./derivations/hierarchy";
import { useActivePointer, useCampaign, useEntry, useRegistry } from "./hooks/useRegistry";
import { useViewMemory } from "./state/view-memory";

interface WorkspaceState {
  // Null while a check-in is viewed: an unstarted campaign has no cycle, so nothing of a run shows.
  viewedPath: CyclePath | null;
  checkin: { draftId: string | null } | null;
  cycleId: string | null;
  campaignId: string | null;
  leafCampaignId: string | null;
  leafCycleId: string | null;
  viewedCandidateId: string | null;
  // An offset in the viewed LEAF's ledger (`StepFacts.offset`); null is the head.
  at: number | null;
  views: readonly View[];
  // The subject is a path the operator picked, not the active run followed.
  pinned: boolean;
  // `resume` is a campaign ROOT row's: view memory may deepen the path.
  navigate: (subject: ViewedSubject, opts?: { resume?: boolean }) => void;
  // Opened again it moves to the end, never shows twice; an omitted key keeps the one it had.
  open: (family: FamilyId, key?: string | null) => void;
  // The view the last `open` asked to see: a fresh object per ask, so an open view is scrolled to again.
  reveal: { family: FamilyId } | null;
  close: (family: FamilyId) => void;
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

// What every pipeline graph lights and the one `NodeDetail` shows: the open `node` view's.
export function useViewedNode(): SelectedNode | null {
  const view = useWorkspace().views.find((v) => v.family === "node");
  return view?.key ? nodeOfKey(view.key) : null;
}

// The ROOT hop's dataset; a drilled-in leaf's is `useLeafDatasetName`.
export function useViewedDatasetName(): string | null {
  const { campaignId, cycleId } = useWorkspace();
  const root = campaignId && cycleId ? encodeCyclePath([{ campaignId, cycleId }]) : null;
  return tagValue(useEntry(root), "dataset");
}

// The viewed subject's listing: a check-in's unstarted campaign by its draft id, else the run's
// campaign. Null is no subject listed: `#/new`, or a listing not landed yet.
export function useViewedListing(): ListedCampaign | null {
  const { checkin, campaignId } = useWorkspace();
  const { unstarted } = useRegistry();
  const campaign = useCampaign(checkin === null ? campaignId : null);
  if (checkin === null) return campaign;
  return unstarted.find((c) => c.address === checkin.draftId) ?? null;
}

const FOLLOWING: Address = { subject: null, views: DEFAULT_VIEWS };

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [screen, setScreen] = useState<Address>(FOLLOWING);
  const [initialized, setInitialized] = useState(false);
  const [goneAddress, setGoneAddress] = useState<string | null>(null);
  const [reveal, setReveal] = useState<{ family: FamilyId } | null>(null);

  const { byAddress } = useRegistry();
  const { active } = useActivePointer();
  const { viewFor, recordView } = useViewMemory();

  // A mount effect, not a useState initializer: the static-export HTML and first client render must agree.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const a = parseAddress(window.location.hash);
    if (a) setScreen(a);
    setInitialized(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // The writer below no-ops when the hash already matches, so this cannot loop against it.
  useEffect(() => {
    const onHash = () => {
      const a = parseAddress(window.location.hash);
      if (a) setScreen(a);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const { subject, views } = screen;
  const pinned = subject !== null && "path" in subject ? subject : null;
  const checkin = subject !== null && "draftId" in subject ? subject : null;

  // A pin moves only onto a fork of its OWN campaign; the first pointer only sets the baseline.
  const pointer = active ? `${active.campaign_id}::${active.cycle_id}` : null;
  const [prevPointer, setPrevPointer] = useState<string | null>(null);
  if (pointer !== null && pointer !== prevPointer) {
    setPrevPointer(pointer);
    const root = pinned ? pathRoot(pinned.path) : null;
    const forkOfPinned =
      root !== null &&
      active !== null &&
      root.campaignId === active.campaign_id &&
      root.cycleId !== active.cycle_id;
    if (prevPointer !== null && forkOfPinned) setScreen((s) => ({ ...s, subject: null }));
  }

  // Memoized: consumers key reads and chart `options` on its identity.
  const pinnedPath = pinned?.path ?? null;
  const viewedPath: CyclePath | null = useMemo(
    () =>
      subject === null
        ? active
          ? [{ campaignId: active.campaign_id, cycleId: active.cycle_id }]
          : null
        : pinnedPath,
    [subject, active, pinnedPath],
  );

  const rootHop = viewedPath ? pathRoot(viewedPath) : null;
  const campaignId = rootHop?.campaignId ?? null;
  const cycleId = rootHop?.cycleId ?? null;

  const leafHop = viewedPath ? pathLeaf(viewedPath) : null;
  const leafCampaignId = leafHop?.campaignId ?? null;
  const leafCycleId = leafHop?.cycleId ?? null;

  const viewedCandidateId = pinned?.candidateId ?? null;
  const at = pinned?.at ?? null;
  const viewedAddress = viewedPath ? encodeCyclePath(viewedPath) : null;

  // `replaceState`, not `push`: Back leaves the app.
  useEffect(() => {
    if (!initialized) return;
    const want = formatAddress(screen);
    // The default view carries no hash at all, never a bare `#/`.
    const bare = want === EMPTY_ADDRESS;
    const now = window.location.hash;
    if (bare ? now === "" || now === EMPTY_ADDRESS : now === want) return;
    window.history.replaceState(
      null,
      "",
      bare ? window.location.pathname + window.location.search : want,
    );
  }, [initialized, screen]);

  useEffect(() => {
    if (!campaignId || !viewedAddress) return;
    recordView(campaignId, { viewedPath: viewedAddress, viewedCandidateId });
  }, [campaignId, viewedAddress, viewedCandidateId, recordView]);

  const resumed = (path: CyclePath): ViewedSubject => {
    const hop = path[0];
    // Only a campaign SWITCH resumes: on the viewed campaign it re-drills forever against the record effect.
    if (path.length !== 1 || !hop || hop.campaignId === campaignId) return { path };
    const mem = viewFor(hop.campaignId);
    const remembered = decodeCyclePath(mem.viewedPath ?? "");
    const root = remembered?.[0];
    if (!remembered || !root || root.campaignId !== hop.campaignId) return { path };
    // Inner hops never appear in `/hierarchy`, so the ROOT hop's existence is the check.
    const known = byAddress.has(encodeCyclePath([root]));
    return known ? { path: remembered, candidateId: mem.viewedCandidateId } : { path };
  };
  // Read by `navigate`, whose identity every consumer holds: a poll's 200 moves `byAddress`.
  const resumedRef = useRef(resumed);
  useEffect(() => {
    resumedRef.current = resumed;
  });

  // The list screen closes on a subject switch; a check-in opens on its chat.
  const navigate = useCallback((to: ViewedSubject, opts: { resume?: boolean } = {}) => {
    const target =
      to !== null && "path" in to && opts.resume && !to.candidateId
        ? resumedRef.current(to.path)
        : to;
    setScreen((s) => ({
      subject: target,
      views:
        target !== null && "draftId" in target
          ? DEFAULT_VIEWS
          : withViews(s.views.filter((v) => v.family !== "campaigns")),
    }));
  }, []);

  const open = useCallback(
    (family: FamilyId, key?: string | null) =>
      startTransition(() => {
        setScreen((s) => {
          const was = s.views.find((v) => v.family === family);
          const view = { family, key: key === undefined ? (was ? was.key : null) : key };
          return { ...s, views: [...s.views.filter((v) => v.family !== family), view] };
        });
        setReveal({ family });
      }),
    [],
  );

  const close = useCallback(
    (family: FamilyId) =>
      setScreen((s) => ({ ...s, views: withViews(s.views.filter((v) => v.family !== family)) })),
    [],
  );

  // Read by `reportAddressGone`, whose identity a polled read holds across renders.
  const pinnedRef = useRef<CyclePath | null>(pinnedPath);
  useEffect(() => {
    pinnedRef.current = pinnedPath;
  });

  // The caller has already confirmed the verdict (`read-cache.ts::GONE_CONFIRM_LIMIT`).
  const reportAddressGone = useCallback(
    (address: string) => {
      const gone = pinnedRef.current;
      if (!gone || encodeCyclePath(gone) !== address) return;
      // Memory would restore the dead address on reload; a reaped `.inner/` leaf's root campaign is still alive.
      recordView(pathRoot(gone).campaignId, { viewedPath: null, viewedCandidateId: null });
      setScreen((s) => ({ ...s, subject: null }));
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
      checkin,
      cycleId,
      campaignId,
      leafCampaignId,
      leafCycleId,
      viewedCandidateId,
      at,
      views,
      pinned: pinned !== null,
      navigate,
      open,
      reveal,
      close,
      reportAddressGone,
      goneAddress,
    }),
    [
      viewedPath,
      checkin,
      cycleId,
      campaignId,
      leafCampaignId,
      leafCycleId,
      viewedCandidateId,
      at,
      views,
      pinned,
      navigate,
      open,
      reveal,
      close,
      reportAddressGone,
      goneAddress,
    ],
  );
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
