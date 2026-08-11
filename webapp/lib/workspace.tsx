"use client";
// Single source of truth for workspace identity. One provider polls the
// server's active pointer (`/sessions/active`) on the live 2 s beat and the
// heavier cycle list (`/cycles`) + campaign registry (`/campaigns`) on a 10 s
// floor; every surface — AppShell, CyclePicker, Sidebar — subscribes here via
// `useWorkspace()` instead of fetching those endpoints on its own. The two
// cadences are deliberate: the active pointer is staleness-critical (a CLI mint
// must yank the viewed unit over promptly), the registry list is not.
//
// "What am I looking at?" has ONE answer: `viewedPath`, a `CyclePath` (see
// `lib/ids.ts`) — the chain of `(campaign, cycle)` hops from the top-level root
// down to the leaf. A top-level cycle is a 1-hop path; an L4 inner loop is a
// 2-hop path (outer → inner); L5+ nests deeper. The DASHBOARD stream re-roots to
// the LEAF hop; chat, selection, dataset, and files bind to the ROOT hop (the
// `campaignId`/`cycleId` exports) — so the conversation stays on the outer thread
// while the dashboard follows an inner loop, with no separate state axis.
//
// `following` is the explicit follow-vs-pin state. While following, the viewed
// path tracks the server's active pointer (always a top-level 1-hop cycle —
// inner runs are sandboxed and never rewrite the global pointer). Picking a unit
// pins an explicit `pinnedPath` (`following=false`); `followActive()` resumes.
// The URL `?path=` param is written ONLY while pinned and stripped while
// following.

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
  decodeCyclePath,
  encodeCyclePath,
  pathLeaf,
  pathRoot,
  type CyclePath,
} from "./ids";
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation, useRevalidation } from "./revalidate";
import { useAuthGate } from "./auth-context";
import { isSelfOptimization } from "./derivations";
import { isInFlight, dockPriority } from "./run-phase";

interface WorkspaceState {
  sessionId: string | null;
  activeCycleId: string | null; // server pointer (active_session.json)
  activeCampaignId: string | null; // campaign of the server pointer
  // The viewed cycle address — the single "what am I looking at". Null until the
  // pointer / pin resolves. Its root hop is the top-level cycle; a deeper leaf is
  // an L4 inner descendant.
  viewedPath: CyclePath | null;
  cycleId: string | null; // the ROOT hop's cycle (chat/dataset/files anchor)
  // The campaign the root hop belongs to — authoritative, never derived from a
  // bare `cycle_id` (a cycle_id is unique only within its campaign). Null until
  // the pointer / pin resolves.
  campaignId: string | null;
  // The LEAF hop — what the dashboard / live feed / samples / selection follow
  // (the inner cycle when drilled into an L4 loop, else identical to the root
  // hop). Derived once here so no surface re-inlines `viewedPath[len-1]`.
  leafCampaignId: string | null;
  leafCycleId: string | null;
  // Does the VIEWED course optimize prompts — i.e. are its samples whole inner
  // campaigns rather than scored rows? Read off the LEAF's campaign, never the
  // root's: a fork of a pp-self campaign is still depth 1 and still self-optimizing,
  // while drilling INTO an inner run puts an ordinary benchmark campaign on the leaf,
  // which is not in `campaigns` (it lives in a sandbox) and correctly reads false.
  // Derived here because three surfaces branch on it and each had re-run the lookup.
  leafIsL4: boolean;
  // The candidate NODE the tree is parked on inside the leaf course, by its served `id`,
  // or null for the course itself. A candidate is a tier of the lineage tree but never a
  // hop of a path — the course carries the address — so it rides beside `viewedPath`
  // rather than inside it.
  //
  // An ID, never a LABEL. `C1.1` is a course's PRIVATE position and every course mints
  // one, so a label addresses nothing across a campaign; the id is minted and unique. Both
  // halves of the address are read off the node that was clicked — nothing builds one.
  //
  // This is NAVIGATION: "which node's children do the bars plot". It is NOT
  // `SelectionContext.candidate`, which is INSPECTION: "which bar is lit". Only the tree
  // writes this one; a bar click writes the other. They were one slot, which made the
  // chart its own input — clicking a bar re-plotted the chart under the operator's cursor.
  viewedCandidateId: string | null;
  datasetName: string | null;
  following: boolean; // the viewed path tracks the active pointer
  cycles: CycleListEntry[];
  cyclesLoaded: boolean; // first /cycles poll has resolved (success or fail)
  // True once the campaign list on hand reflects the CURRENT lifecycleFilter.
  // Flips false the instant the filter changes (Active⇄Archived) until the
  // refetch for the new filter lands — so the sidebar shows `loading…` instead
  // of the prior tab's stale list.
  campaignsLoaded: boolean;
  cyclesError: string | null;
  // The in-flight subset of `cycles` (running / gate / paused — `detached`
  // is deliberately excluded, it means a dead producer, I6) — derived once
  // here, membership AND order, so every "what's running" surface reads the
  // same list instead of re-filtering or re-sorting its own copy.
  liveCycles: CycleListEntry[];
  // Campaign manifests (GET /campaigns) — polled in the same tick as
  // /cycles. Carries the operator-editable `label`; surfaces resolve a
  // campaign's display name from here. Last-good list survives a failed tick.
  campaigns: CampaignSummary[];
  activeError: string | null;
  // Operator's lifecycle filter — drives both the poll's `?lifecycle=`
  // query and the sidebar's "Active / Archived" tab. Default `active`.
  // Persisted nowhere; resets per visit (matches dataset filter behaviour).
  lifecycleFilter: LifecycleFilter;
  setLifecycleFilter: (f: LifecycleFilter) => void;
  // Pin an explicit cycle address → following=false. The general verb; a hop
  // can be top-level or an inner descendant. `candidate` parks the tree on one of that
  // course's candidates; omitting it means the course itself.
  selectCyclePath: (path: CyclePath, candidateId?: string | null) => void;
  // Convenience: pin a top-level (1-hop) cycle. Both ids required — a cycle_id
  // alone is ambiguous across campaigns.
  selectCycle: (campaignId: string, cycleId: string) => void;
  // Open a run that lives in the VIEWED leaf's sandbox — one hop deeper than what
  // is on screen. The L4 surfaces (a cell bar, a panel row) name a run and mean
  // "descend into it"; they don't own the address, so they don't build the path.
  drillInto: (campaignId: string, cycleId: string) => void;
  // Drop back to the top-level (root) cycle from an inner descendant, keeping it
  // pinned. No-op at depth 1.
  backToOuter: () => void;
  followActive: () => void; // un-pin → snap back to the active pointer
  // The address the server says no longer exists — reported by whichever poll owns
  // the AUTHORITATIVE read for it, acted on here. This is the only way a pin dies
  // without the operator clicking: a campaign can be deleted (root CLAUDE.md calls
  // deleting what you are looking at "the ordinary case"), an `.inner/` sandbox can
  // be reaped with no user action at all, or a store can be reset under a bookmark.
  //
  // Detection is the address's OWN read, never list membership: an L4 inner hop is
  // absent from `/cycles` (it lives in a sandbox) and an archived campaign is absent
  // from the `active` filter, so membership would kill two live addresses.
  // Archived ≠ gone.
  reportAddressGone: (address: string) => void;
  // Set for the surface that announces the recovery; null once acknowledged.
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

// Reconnect cadence while the API is unreachable — matches lib/poll.tsx's
// RECONNECT_INTERVAL_MS so both polls retry a downed server on the same 5 s beat.
const RECONNECT_INTERVAL_MS = 5000;

// The deep-link, read as ONE address. `webapp/CLAUDE.md` says the address is
// `(path, candidateId)`, and the URL used to encode only the first half — so a reload
// silently dropped the node the tree was parked on. Harmless-looking for an ordinary
// course (its own path still names a node), but a FORK is not a node: its path names
// something the server dissolved onto the parent's timeline, so a fork path with no
// candidate names nothing at all and the bars came back empty.
//
// Two params rather than one encoded string: `encodeCyclePath` is also the wire format for
// the server's `?descend=`, and a candidate has no business in that.
function urlAddress(): { path: CyclePath; candidateId: string | null } | null {
  if (typeof window === "undefined") return null;
  const params = new URLSearchParams(window.location.search);
  const path = params.get("path");
  const decoded = path ? decodeCyclePath(path) : null;
  if (!decoded) return null;
  return { path: decoded, candidateId: params.get("cand") };
}

export function WorkspaceProvider({
  // Registry-list cadence (`/cycles` + `/campaigns`) — the passive floor for
  // rarely-changing data. The active-pointer poll runs faster (below).
  intervalMs = 10000,
  // Active-pointer cadence (`/sessions/active`) — matches the dashboard's 2 s
  // live beat so a CLI-minted cycle is followed without the registry's lag.
  pointerIntervalMs = 2000,
  children,
}: {
  intervalMs?: number;
  pointerIntervalMs?: number;
  children: ReactNode;
}) {
  // The explicit pin — the viewed path while not following (null while
  // following). Both the top-level and inner-descendant selections live here as
  // one address; there is no separate inner-focus axis.
  const [pinnedPath, setPinnedPath] = useState<CyclePath | null>(null);
  // Scoped to `pinnedPath` — every write below sets the pair, so a candidate can never
  // outlive the course that owns it.
  const [viewedCandidateId, setViewedCandidateId] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  // The `?path=` deep-link is read in a mount effect rather than a useState
  // initializer so the static-export HTML and the first client render agree (no
  // hydration mismatch).
  const [initialized, setInitialized] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null);
  const [cycles, setCycles] = useState<CycleListEntry[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([]);
  // Which lifecycle filter the current `campaigns` reflect — null until the
  // first fetch lands. `campaignsLoaded` is this matching the live filter.
  const [campaignsFilter, setCampaignsFilter] = useState<LifecycleFilter | null>(
    null,
  );
  const [cyclesLoaded, setCyclesLoaded] = useState(false);
  const [cyclesError, setCyclesError] = useState<string | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);
  const [lifecycleFilter, setLifecycleFilter] =
    useState<LifecycleFilter>("active");
  // The address that just stopped existing — drives the one-shot recovery notice.
  const [goneAddress, setGoneAddress] = useState<string | null>(null);

  // Poll only with a confirmed session; a 401 on any of the three reads
  // re-probes /auth/me so the loop halts when the session dies (useAuthGate).
  const { authed, onAuthError } = useAuthGate();

  // Tracks the active pointer from the last successful poll. When the
  // server-side pointer transitions to a *different* cycle (CLI ran
  // `new`, fork, or sweep — all three mint a fresh cycle id and
  // re-write active_session.json), we auto-snap follow=true so the
  // viewed unit yanks to the new session. Resume does not move the
  // pointer, so a pinned operator studying a finished cycle stays put.
  // Ref (not state) so the comparison happens inline in the tick
  // callback without re-render churn.
  const prevActivePointerRef = useRef<string | null>(null);

  // Mount: honour a `?path=` deep-link as an explicit pin.
  // The synchronous setState here is load-bearing, not an oversight — the
  // deep-link is read in a mount effect (not a useState initializer) so
  // the static-export HTML and the first client render agree, then
  // corrected post-hydration. See the `initialized` comment above. This
  // is the one place set-state-in-effect is deliberately waived.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const deepLink = urlAddress();
    if (deepLink) {
      setPinnedPath(deepLink.path);
      setViewedCandidateId(deepLink.candidateId);
      setFollowing(false);
    }
    setInitialized(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // TWO poll cadences. The active pointer (`/sessions/active` — three opaque
  // ids) is staleness-critical: when the CLI mints a fresh cycle
  // (`new`/fork/sweep all rewrite active_session.json) the viewed unit must
  // yank to it promptly, so it rides the live dashboard's 2 s beat. The cycle
  // list + campaign registry (`/cycles` + `/campaigns`) is heavier and changes
  // rarely, so it stays on the 10 s floor. Polling them together at 10 s was
  // the stale-window bug: pointer discovery lagged the 2 s data poll by up to
  // 10 s, so a CLI-minted run left the dashboard pinned to the dead cycle until
  // the next registry tick. `usePoll` owns each loop's interval, hidden-tab
  // pause, focus wake (a `new` run in another terminal shows within a frame),
  // and per-tick aborts; a `useRevalidation` bump forces an immediate re-tick
  // on both so an in-app fork / stop lands without a poll-interval wait.
  const reval = useRevalidation();

  // Fast loop — the active pointer + the follow-snap. Sole owner of the pointer
  // state; the registry loop no longer second-guesses it from `/cycles`.
  const pointerTick = useCallback(
    async (signal: AbortSignal) => {
      let active;
      try {
        active = await fetchActive(signal);
      } catch (err) {
        if (signal.aborted) return;
        // A 401 means the session died — re-probe /auth/me so the gate flips
        // unauthed and the loop stops instead of storming.
        onAuthError(err);
        // "No active session" is not an error and never reaches here: the route
        // serves it as null ids on a 200 (`active.py::get_active_session`), so the
        // success path below clears the pointer. Anything caught here is a real
        // reachability failure and belongs in activeError.
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
      // Auto-snap to the active pointer when it transitions to a fresh cycle.
      // The first poll's `prev === null` establishes the baseline (no snap);
      // subsequent transitions are CLI-driven mints. A deliberately-pinned
      // operator running `new` opted into the new session by issuing the command.
      if (nextActiveCycle && nextActiveCampaign) {
        const nextPointer = `${nextActiveCampaign}::${nextActiveCycle}`;
        const prevPointer = prevActivePointerRef.current;
        if (prevPointer !== null && prevPointer !== nextPointer) {
          setFollowing(true);
          setPinnedPath(null); // a fresh outer mint invalidates any pin
          setViewedCandidateId(null);
        }
        prevActivePointerRef.current = nextPointer;
      }
    },
    [onAuthError],
  );

  // Slow loop — the cycle list + campaign registry that back the picker and
  // sidebar. Keeps the last good list on a failed tick.
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

  // When BOTH the pointer and the list fail, the API is unreachable — back
  // both loops off to the 5 s reconnect probe. Either succeeding = reachable.
  const wsOffline = activeError != null && cyclesError != null;
  usePoll(pointerTick, {
    intervalMs: wsOffline ? RECONNECT_INTERVAL_MS : pointerIntervalMs,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: reval,
  });
  usePoll(registryTick, {
    intervalMs: wsOffline ? RECONNECT_INTERVAL_MS : intervalMs,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: reval,
  });

  // The viewed path: the server pointer (a top-level 1-hop cycle) while
  // following, else the explicit pin. Memoized because it is an ADDRESS, and an
  // address that changes identity every render is not one: consumers key polls,
  // memos and callbacks on it (`useLineageTree`, the candidates card's `onSelect` —
  // which rides the chart's `options` memo, so a fresh array here forced a
  // `chart.update()` on every 2 s pointer tick).
  const viewedPath: CyclePath | null = useMemo(
    () =>
      following
        ? activeCampaignId && activeCycleId
          ? [{ campaignId: activeCampaignId, cycleId: activeCycleId }]
          : null
        : pinnedPath,
    [following, activeCampaignId, activeCycleId, pinnedPath],
  );

  // The root hop — what chat / selection / dataset / files bind to. campaignId
  // is authoritative on both sides — never inferred from a bare cycle_id.
  const rootHop = viewedPath ? pathRoot(viewedPath) : null;
  const campaignId = rootHop?.campaignId ?? null;
  const cycleId = rootHop?.cycleId ?? null;

  // The leaf hop — the dashboard / feed / samples anchor. Identical to the root
  // hop at depth 1; the inner cycle when drilled into an L4 loop.
  const leafHop = viewedPath ? pathLeaf(viewedPath) : null;
  const leafCampaignId = leafHop?.campaignId ?? null;
  const leafCycleId = leafHop?.cycleId ?? null;
  const leafIsL4 = isSelfOptimization(
    campaigns.find((c) => c.campaign_id === leafCampaignId)?.backend_type,
  );

  // Dataset of the root hop: the cycle-list row matched on BOTH ids.
  const cycleEntry =
    cycleId && campaignId
      ? (cycles.find(
          (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
        ) ?? null)
      : null;
  const datasetName = cycleEntry?.dataset_name ?? null;

  // One workspace-wide "what is running" derivation — membership AND order,
  // so every dock reader shares both for free instead of re-sorting its own
  // copy (frontend-surface-contract I6: one shared ordering). Recomputed only
  // when the list mutates.
  //
  // Ordered by WHAT NEEDS YOU, not by what is busy — see `dockPriority`.
  const liveCycles = useMemo(
    () =>
      cycles
        .filter((c) => isInFlight(c.run_phase))
        .sort((a, b) => dockPriority(a.run_phase) - dockPriority(b.run_phase)),
    [cycles],
  );

  // URL contract: `?path=<encoded CyclePath>` (+ `?cand=<id>`) present ⇔ pinned to that
  // address. Written only while pinned, stripped while following. BOTH halves, so a reload
  // restores the same address rather than half of one.
  useEffect(() => {
    if (!initialized || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const wantPath = following || !pinnedPath ? null : encodeCyclePath(pinnedPath);
    const wantCand = wantPath ? viewedCandidateId : null;
    if (params.get("path") === wantPath && params.get("cand") === wantCand) return;
    if (wantPath) params.set("path", wantPath);
    else params.delete("path");
    if (wantCand) params.set("cand", wantCand);
    else params.delete("cand");
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [initialized, following, pinnedPath, viewedCandidateId]);

  const selectCyclePath = useCallback(
    (path: CyclePath, candidate: string | null = null) => {
      setFollowing(false);
      setPinnedPath(path);
      setViewedCandidateId(candidate);
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

  // Drop back to the top-level cycle from an inner descendant, keeping it
  // pinned. No-op at depth 1 (and while following — a follow view is always a
  // 1-hop pointer, so there is nothing to pop).
  const backToOuter = useCallback(() => {
    setPinnedPath((prev) => (prev && prev.length > 1 ? prev.slice(0, 1) : prev));
    setViewedCandidateId(null);
  }, []);

  const followActive = useCallback(() => {
    setFollowing(true);
    setPinnedPath(null);
    setViewedCandidateId(null);
  }, []);

  // The pin, mirrored into a ref so `reportAddressGone` keeps a STABLE identity.
  // Its callers are poll ticks, and `usePoll` restarts its loop when the tick's
  // identity changes — a callback that churned with every pin would re-arm the
  // very polls this exists to quiet.
  const pinnedRef = useRef<CyclePath | null>(pinnedPath);
  useEffect(() => {
    pinnedRef.current = pinnedPath;
  });

  // The ONE place a dead address dies. Unpins and resumes following, so the view
  // lands somewhere real instead of hanging on an address that will never answer.
  //
  // The caller has already CONFIRMED the verdict (one 404 during a mint race is
  // normal; see `poll.tsx`'s consecutive-miss guard) — this only checks that the
  // report still describes what we are actually viewing, so a late report from a
  // superseded address cannot yank a pin the operator has since moved on from.
  const reportAddressGone = useCallback((address: string) => {
    const pinned = pinnedRef.current;
    if (!pinned || encodeCyclePath(pinned) !== address) return;
    setPinnedPath(null);
    setViewedCandidateId(null);
    setFollowing(true);
    setGoneAddress(address);
  }, []);

  const dismissGoneNotice = useCallback(() => setGoneAddress(null), []);

  // Switching tabs re-queries `/campaigns?lifecycle=` — bump revalidation so the
  // registry loop re-ticks at once instead of waiting out its 10 s interval.
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
    cycles,
    cyclesLoaded,
    campaignsLoaded: campaignsFilter === lifecycleFilter,
    cyclesError,
    liveCycles,
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
