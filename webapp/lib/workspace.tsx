"use client";
// Single source of truth for workspace identity. One provider polls the
// server's active pointer (`/sessions/active`) and the cycle list (`/cycles`)
// together; every surface — AppShell, CyclePicker, Sidebar —
// subscribes here via `useWorkspace()` instead of fetching those
// endpoints on its own.
//
// Workspace identity is four-level: dataset → campaign → unit, with the
// operator's active pointer the lens into them. A `cycle_id` is unique
// ONLY within its campaign — re-running `new` on one dataset yields many
// campaigns whose root unit shares the same `cycle_id`. So a unit's
// identity is the PAIR `(campaignId, cycleId)`; selection, lookup, and
// every per-cycle fetch carry both. Resolving by `cycle_id` alone picks
// an arbitrary campaign and breaks navigation.
//
// `following` is the explicit follow-vs-pin state. While following,
// `(campaignId, cycleId)` track the server's active pointer in lockstep.
// Picking a unit pins it (`following=false`); `followActive()` resumes.
// The URL `?campaign=&cycle=` params are written ONLY while pinned and
// stripped while following.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
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
import { usePoll } from "./hooks/usePoll";
import { useRevalidation } from "./revalidate";
import { useAuthGate } from "./auth-context";

export interface WorkspaceState {
  sessionId: string | null;
  activeCycleId: string | null; // server pointer (active_session.json)
  activeCampaignId: string | null; // campaign of the server pointer
  cycleId: string | null; // the unit being VIEWED
  // The campaign + dataset the viewed unit belongs to. A unit is the pair
  // `(campaignId, cycleId)` — campaignId is authoritative, never derived
  // from a `cycle_id`-only lookup. Null until the pointer / pin resolves.
  campaignId: string | null;
  datasetName: string | null;
  following: boolean; // (campaignId, cycleId) track the active pointer
  cycles: CycleListEntry[];
  cyclesLoaded: boolean; // first /cycles poll has resolved (success or fail)
  cyclesError: string | null;
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
  // User pin → following=false. Both ids required: a cycle_id alone is
  // ambiguous across campaigns.
  selectCycle: (campaignId: string, cycleId: string) => void;
  followActive: () => void; // un-pin → snap back to the active pointer
}

interface UnitPin {
  campaignId: string;
  cycleId: string;
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

function urlPin(): UnitPin | null {
  if (typeof window === "undefined") return null;
  const p = new URLSearchParams(window.location.search);
  const campaignId = p.get("campaign");
  const cycleId = p.get("cycle");
  return campaignId && cycleId ? { campaignId, cycleId } : null;
}

export function WorkspaceProvider({
  intervalMs = 10000,
  children,
}: {
  intervalMs?: number;
  children: ReactNode;
}) {
  const [pinned, setPinned] = useState<UnitPin | null>(null);
  const [following, setFollowing] = useState(true);
  // The `?campaign=&cycle=` deep-link is read in a mount effect rather than
  // a useState initializer so the static-export HTML and the first client
  // render agree (no hydration mismatch).
  const [initialized, setInitialized] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  const [activeCampaignId, setActiveCampaignId] = useState<string | null>(null);
  const [cycles, setCycles] = useState<CycleListEntry[]>([]);
  const [campaigns, setCampaigns] = useState<CampaignSummary[]>([]);
  const [cyclesLoaded, setCyclesLoaded] = useState(false);
  const [cyclesError, setCyclesError] = useState<string | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);
  const [lifecycleFilter, setLifecycleFilter] =
    useState<LifecycleFilter>("active");

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

  // Mount: honour a `?campaign=&cycle=` deep-link as an explicit pin.
  // The synchronous setState here is load-bearing, not an oversight — the
  // deep-link is read in a mount effect (not a useState initializer) so
  // the static-export HTML and the first client render agree, then
  // corrected post-hydration. See the `initialized` comment above. This
  // is the one place set-state-in-effect is deliberately waived.
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const deepLink = urlPin();
    if (deepLink) {
      setPinned(deepLink);
      setFollowing(false);
    }
    setInitialized(true);
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  // One poll — active pointer, cycle list, and campaign registry move
  // together so the list, the `●` pointer, and the sidebar can never
  // disagree. The 10 s `intervalMs` is the *passive* floor: this is
  // registry-level data that changes rarely, so it doesn't need the live
  // dashboard's 2 s cadence. Operator actions bypass it — `usePoll` owns
  // the interval, the hidden-tab pause, the focus wake (a `new` run in
  // another terminal shows within a frame), and per-tick aborts; a mutation
  // bump (`useRevalidation`) forces an immediate re-tick so a fork / stop
  // lands without a poll-interval wait.
  const tick = useCallback(async (signal: AbortSignal) => {
    const [activeRes, cyclesRes, campaignsRes] = await Promise.allSettled([
      fetchActive(signal),
      fetchCycles(signal),
      fetchCampaigns(undefined, signal, lifecycleFilter),
    ]);
    if (signal.aborted) return;
    // A 401 on any read means the session died — re-probe /auth/me so the
    // gate flips unauthed and this loop stops instead of storming 401s.
    for (const r of [activeRes, cyclesRes, campaignsRes]) {
      if (r.status === "rejected") onAuthError(r.reason);
    }
    let nextActiveCycle: string | null = null;
    let nextActiveCampaign: string | null = null;
    if (activeRes.status === "fulfilled") {
      setSessionId(activeRes.value.session_id || null);
      nextActiveCycle = activeRes.value.cycle_id || null;
      nextActiveCampaign = activeRes.value.campaign_id || null;
      setActiveCycleId(nextActiveCycle);
      setActiveCampaignId(nextActiveCampaign);
      setActiveError(null);
    } else {
      setActiveError(
        (activeRes.reason as Error)?.message ?? "active session unavailable",
      );
    }
    if (cyclesRes.status === "fulfilled") {
      setCycles(cyclesRes.value.cycles);
      // `/cycles` also carries the active pointer — use it as a fallback
      // only when `/sessions/active` itself failed this tick.
      if (activeRes.status !== "fulfilled") {
        if (cyclesRes.value.active_cycle_id) {
          nextActiveCycle = cyclesRes.value.active_cycle_id;
          setActiveCycleId(nextActiveCycle);
        }
        if (cyclesRes.value.active_campaign_id) {
          nextActiveCampaign = cyclesRes.value.active_campaign_id;
          setActiveCampaignId(nextActiveCampaign);
        }
      }
      setCyclesError(null);
    } else {
      setCyclesError(
        (cyclesRes.reason as Error)?.message ?? "campaign list unavailable",
      );
    }
    // Auto-snap to the active pointer when it transitions to a fresh
    // cycle. The first poll's `prev === null` establishes the baseline
    // (no snap); subsequent transitions are CLI-driven mints. A
    // deliberately-pinned operator running `new` opted in to the new
    // session by issuing the command.
    if (nextActiveCycle && nextActiveCampaign) {
      const nextPointer = `${nextActiveCampaign}::${nextActiveCycle}`;
      const prevPointer = prevActivePointerRef.current;
      if (prevPointer !== null && prevPointer !== nextPointer) {
        setFollowing(true);
        setPinned(null);
      }
      prevActivePointerRef.current = nextPointer;
    }
    // Campaign registry — keep the last good list on a failed tick.
    if (campaignsRes.status === "fulfilled") {
      setCampaigns(campaignsRes.value.campaigns);
    }
    setCyclesLoaded(true);
  }, [lifecycleFilter, onAuthError]);
  // Two cadences, mirroring the dashboard poll: when both the active pointer
  // and the cycle list fail, the API is unreachable — back off to the 5 s
  // reconnect probe instead of hammering 2 s. Either succeeding = reachable.
  const wsOffline = activeError != null && cyclesError != null;
  usePoll(tick, {
    intervalMs: wsOffline ? RECONNECT_INTERVAL_MS : intervalMs,
    tickOnFocus: true,
    enabled: authed,
    revalidateOn: useRevalidation(),
  });

  // The viewed unit: the server pointer while following, else the pin.
  // campaignId is authoritative on both sides — never inferred from a
  // bare cycle_id.
  const cycleId = following ? activeCycleId : (pinned?.cycleId ?? null);
  const campaignId = following ? activeCampaignId : (pinned?.campaignId ?? null);

  // Dataset of the viewed unit: the cycle-list row matched on BOTH ids.
  const cycleEntry =
    cycleId && campaignId
      ? (cycles.find(
          (c) => c.campaign_id === campaignId && c.cycle_id === cycleId,
        ) ?? null)
      : null;
  const datasetName = cycleEntry?.dataset_name ?? null;

  // URL contract: `?campaign=X&cycle=Y` present ⇔ pinned to that unit.
  // Written only while pinned, stripped while following.
  useEffect(() => {
    if (!initialized || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const wantCampaign = following ? null : (pinned?.campaignId ?? null);
    const wantCycle = following ? null : (pinned?.cycleId ?? null);
    if (
      params.get("campaign") === wantCampaign &&
      params.get("cycle") === wantCycle
    ) {
      return;
    }
    if (wantCampaign && wantCycle) {
      params.set("campaign", wantCampaign);
      params.set("cycle", wantCycle);
    } else {
      params.delete("campaign");
      params.delete("cycle");
    }
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [initialized, following, pinned]);

  const selectCycle = useCallback((cid: string, cyid: string) => {
    setFollowing(false);
    setPinned({ campaignId: cid, cycleId: cyid });
  }, []);

  const followActive = useCallback(() => {
    setFollowing(true);
    setPinned(null);
  }, []);

  const value: WorkspaceState = {
    sessionId,
    activeCycleId,
    activeCampaignId,
    cycleId,
    campaignId,
    datasetName,
    following,
    cycles,
    cyclesLoaded,
    cyclesError,
    campaigns,
    activeError,
    lifecycleFilter,
    setLifecycleFilter,
    selectCycle,
    followActive,
  };
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
