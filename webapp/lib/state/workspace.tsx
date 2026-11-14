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
} from "@/lib/ids";
import {
  DEFAULT_VIEWS,
  EMPTY_ADDRESS,
  NO_KEYS,
  formatAddress,
  formatPick,
  nodeOfKey,
  parseAddress,
  parsePick,
  pickedAddressOf,
  withViews,
  type Address,
  type AddressRefusal,
  type CandidatePick,
  type FamilyId,
  type SelectedNode,
  type View,
  type ViewKeys,
  type ViewedSubject,
} from "@/lib/address";
import type { ListedCampaign } from "@/lib/api/types";
import { GONE_NOTICE_MS } from "@/lib/cadence";
import { tagValue } from "@/lib/derivations";
import { useActivePointer, useCampaign, useEntry, useRegistry } from "@/lib/hooks/useRegistry";
import { useViewMemory } from "./view-memory";

type Hop = CyclePath[number];

// The viewed cycle, once: its whole path, that path's encoded `address` (every read's and the
// hierarchy's course key), and the ROOT hop (ingest, Files, view memory) beside the LEAF hop (display).
export interface ViewedCycle {
  path: CyclePath;
  address: string;
  root: Hop;
  leaf: Hop;
}

interface WorkspaceState {
  // Null while a check-in is viewed: an unstarted campaign has no cycle, so nothing of a run shows.
  viewed: ViewedCycle | null;
  checkin: { draftId: string | null } | null;
  // The one candidate pick, on the viewed path (the hash's `/r{round}/{arm}`); `address` is its hierarchy entry's.
  pick: (CandidatePick & { address: string }) | null;
  // The pick's one writer: on `path` (the viewed one when omitted), keeping the moment while the
  // path stays the viewed one; null unpicks.
  pickCandidate: (pick: CandidatePick | null, path?: CyclePath) => void;
  // An offset in the viewed LEAF's ledger (`StepFacts.offset`); null is the head.
  at: number | null;
  views: readonly View[];
  keys: ViewKeys;
  // The view keys' one writer. A round other than the pick's unpicks: a pick reads its own round.
  setViewKeys: (patch: Partial<ViewKeys>) => void;
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
  goneAddress: GoneAddress | null;
}

// An address the workspace just left or would not open, said for `GONE_NOTICE_MS`: a pinned
// cycle whose campaign is gone, or a hash naming no address this version reads, which stays in
// the bar until the screen moves.
export interface GoneAddress {
  address: string;
  cause: "gone" | AddressRefusal;
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
  const { at, viewed } = useWorkspace();
  if (at === null || path === null || viewed === null) return null;
  return encodeCyclePath(path) === viewed.address ? at : null;
}

// What every pipeline graph lights and the one `NodeDetail` shows: the open `node` view's.
export function useViewedNode(): SelectedNode | null {
  const view = useWorkspace().views.find((v) => v.family === "node");
  return view?.key ? nodeOfKey(view.key) : null;
}

// The ROOT hop's dataset; a drilled-in leaf's is `useLeafDatasetName`.
export function useViewedDatasetName(): string | null {
  const { viewed } = useWorkspace();
  return tagValue(useEntry(viewed ? encodeCyclePath([viewed.root]) : null), "dataset");
}

// The viewed subject's listing: a check-in's unstarted campaign by its draft id, else the run's
// campaign. Null is no subject listed: `#/new`, or a listing not landed yet.
export function useViewedListing(): ListedCampaign | null {
  const { checkin, viewed } = useWorkspace();
  const { unstarted } = useRegistry();
  const campaign = useCampaign(checkin === null ? (viewed?.root.campaignId ?? null) : null);
  if (checkin === null) return campaign;
  return unstarted.find((c) => c.address === checkin.draftId) ?? null;
}

const FOLLOWING: Address = { subject: null, views: DEFAULT_VIEWS, keys: NO_KEYS };

function pickOf(subject: ViewedSubject): string | null {
  return subject !== null && "path" in subject && subject.pick ? formatPick(subject.pick) : null;
}

// The keys a move from the viewed *fromPath* to *to* keeps: a new path clears them; a pick changed
// on the same one reads its own round.
function keysAfter(from: Address, fromPath: string | null, to: ViewedSubject): ViewKeys {
  const toPath = to !== null && "path" in to ? encodeCyclePath(to.path) : null;
  if (toPath === null || toPath !== fromPath) return NO_KEYS;
  if (pickOf(to) === pickOf(from.subject)) return from.keys;
  return { ...from.keys, observe: null, round: pickOf(to) || pickOf(from.subject) ? null : from.keys.round };
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [screen, setScreen] = useState<Address>(FOLLOWING);
  const [initialized, setInitialized] = useState(false);
  const [goneAddress, setGoneAddress] = useState<GoneAddress | null>(null);
  const [reveal, setReveal] = useState<{ family: FamilyId } | null>(null);
  // The screen's address as the bar last showed it; null once a hash is read.
  const written = useRef<string | null>(null);

  const { byAddress } = useRegistry();
  const { active } = useActivePointer();
  const { viewFor, recordView } = useViewMemory();

  // A refused hash leaves the screen where it is and says why, naming the hash.
  const openHash = useCallback((hash: string): boolean => {
    const a = parseAddress(hash);
    if ("refused" in a) {
      setGoneAddress({ address: hash, cause: a.refused });
      return false;
    }
    written.current = null;
    setScreen(a);
    return true;
  }, []);

  // A mount effect, not a useState initializer: the static-export HTML and first client render must agree.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (!openHash(window.location.hash)) written.current = formatAddress(FOLLOWING);
    setInitialized(true);
  }, [openHash]);
  /* eslint-enable react-hooks/set-state-in-effect */

  // The writer below no-ops when the hash already matches, so this cannot loop against it.
  useEffect(() => {
    const onHash = () => void openHash(window.location.hash);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [openHash]);

  const { subject, views, keys } = screen;
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
    // A followed run that moves is a new leaf: its keys go with the old one.
    if (prevPointer !== null && (forkOfPinned || pinned === null)) {
      setScreen((s) => ({ ...s, subject: forkOfPinned ? null : s.subject, keys: NO_KEYS }));
    }
  }

  // Memoized: consumers key reads and chart `options` on its identity.
  const pinnedPath = pinned?.path ?? null;
  const viewed: ViewedCycle | null = useMemo(() => {
    const path: CyclePath | null =
      subject === null
        ? active
          ? [{ campaignId: active.campaign_id, cycleId: active.cycle_id }]
          : null
        : pinnedPath;
    return path
      ? { path, address: encodeCyclePath(path), root: pathRoot(path), leaf: pathLeaf(path) }
      : null;
  }, [subject, active, pinnedPath]);
  const campaignId = viewed?.root.campaignId ?? null;
  const viewedAddress = viewed?.address ?? null;

  const pinnedPick = pinned?.pick ?? null;
  const pick = useMemo(
    () => (viewed && pinnedPick ? { ...pinnedPick, address: pickedAddressOf(viewed.address, pinnedPick) } : null),
    [viewed, pinnedPick],
  );
  const viewedPick = pinnedPick ? formatPick(pinnedPick) : null;
  const at = pinned?.at ?? null;

  // `replaceState`, not `push`: Back leaves the app.
  useEffect(() => {
    if (!initialized) return;
    const want = formatAddress(screen);
    // A read hash is written back canonical; a refused one stays in the bar until the screen moves.
    if (want === written.current) return;
    written.current = want;
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
    recordView(campaignId, { viewedPath: viewedAddress, viewedPick });
  }, [campaignId, viewedAddress, viewedPick, recordView]);

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
    return known ? { path: remembered, pick: parsePick(mem.viewedPick ?? "") } : { path };
  };
  // Read by `navigate`, whose identity every consumer holds: a poll's 200 moves `byAddress`.
  const resumedRef = useRef(resumed);
  useEffect(() => {
    resumedRef.current = resumed;
  });

  // Read by `navigate` and `pickCandidate`, whose identities every picker holds.
  const hereRef = useRef({ viewed, at });
  useEffect(() => {
    hereRef.current = { viewed, at };
  });

  // The list screen closes on a subject switch; a check-in opens on its chat.
  const navigate = useCallback((to: ViewedSubject, opts: { resume?: boolean } = {}) => {
    const target =
      to !== null && "path" in to && opts.resume && !to.pick
        ? resumedRef.current(to.path)
        : to;
    setScreen((s) => ({
      subject: target,
      views:
        target !== null && "draftId" in target
          ? DEFAULT_VIEWS
          : withViews(s.views.filter((v) => v.family !== "campaigns")),
      keys: keysAfter(s, hereRef.current.viewed?.address ?? null, target),
    }));
  }, []);

  const setViewKeys = useCallback(
    (patch: Partial<ViewKeys>) =>
      setScreen((s) => {
        const pinnedPick = s.subject !== null && "path" in s.subject ? s.subject.pick : null;
        const unpicks = patch.round !== undefined && pinnedPick && pinnedPick.round !== patch.round;
        return {
          ...s,
          subject: unpicks && s.subject !== null && "path" in s.subject ? { ...s.subject, pick: null } : s.subject,
          keys: { ...s.keys, ...patch },
        };
      }),
    [],
  );

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

  const pickCandidate = useCallback(
    (next: CandidatePick | null, path?: CyclePath) => {
      const here = hereRef.current;
      const to = path ?? here.viewed?.path;
      if (!to) return;
      const stays = here.viewed !== null && encodeCyclePath(to) === here.viewed.address;
      navigate({ path: to, pick: next, at: stays ? here.at : null });
    },
    [navigate],
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
      recordView(pathRoot(gone).campaignId, { viewedPath: null, viewedPick: null });
      setScreen((s) => ({ ...s, subject: null, keys: NO_KEYS }));
      setGoneAddress({ address, cause: "gone" });
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
      viewed,
      checkin,
      pick,
      pickCandidate,
      at,
      views,
      keys,
      setViewKeys,
      pinned: pinned !== null,
      navigate,
      open,
      reveal,
      close,
      reportAddressGone,
      goneAddress,
    }),
    [
      viewed,
      checkin,
      pick,
      pickCandidate,
      at,
      views,
      keys,
      setViewKeys,
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
