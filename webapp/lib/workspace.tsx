"use client";
// Single source of truth for workspace identity. One provider polls the
// server's active pointer (`/sessions/active`) on the live 2 s beat and the
// heavier cycle list (`/cycles`) + campaign registry (`/campaigns`) on a 10 s
// floor; every surface — AppShell, CyclePicker, Sidebar — subscribes here via
// `useWorkspace()` instead of fetching those endpoints on its own. The two
// cadences are deliberate: the active pointer is staleness-critical (a CLI mint
// must yank the viewed unit over promptly), the registry list is not.
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
import { bumpRevalidation, useRevalidation } from "./revalidate";
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
  // True once the campaign list on hand reflects the CURRENT lifecycleFilter.
  // Flips false the instant the filter changes (Active⇄Archived) until the
  // refetch for the new filter lands — so the sidebar shows `loading…` instead
  // of the prior tab's stale list.
  campaignsLoaded: boolean;
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
  // L4 inner-loop focus. When an operator drills into one inner campaign of a
  // running `promptpotter-self` outer cycle, `innerFocus` names it; the DASHBOARD
  // stream re-roots to that inner cycle's telemetry while the outer stays pinned
  // as the viewed unit (so the chat pane + selection remain on the outer thread —
  // inner loops carry run-telemetry, not an operator conversation). The single
  // re-rootable "focused unit"; null = the dashboard shows the outer cycle.
  innerFocus: InnerFocus | null;
  selectInner: (focus: InnerFocus) => void;
  clearInner: () => void; // back to the outer cycle (keeps the outer pinned)
}

interface UnitPin {
  campaignId: string;
  cycleId: string;
}

export interface InnerFocus {
  outerCampaignId: string;
  outerCycleId: string;
  innerCampaignId: string;
  innerCycleId: string;
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

// The inner-loop deep link — `&inner_campaign=&inner_cycle=` alongside the outer
// `?campaign=&cycle=` pin. Its outer ids come from the pin (an inner focus is
// only valid against a pinned outer cycle).
function urlInner(pin: UnitPin | null): InnerFocus | null {
  if (!pin || typeof window === "undefined") return null;
  const p = new URLSearchParams(window.location.search);
  const innerCampaignId = p.get("inner_campaign");
  const innerCycleId = p.get("inner_cycle");
  return innerCampaignId && innerCycleId
    ? {
        outerCampaignId: pin.campaignId,
        outerCycleId: pin.cycleId,
        innerCampaignId,
        innerCycleId,
      }
    : null;
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
  const [pinned, setPinned] = useState<UnitPin | null>(null);
  const [following, setFollowing] = useState(true);
  // L4 inner-loop focus (null = viewing the outer cycle). Cleared whenever the
  // viewed OUTER unit changes (pick another cycle, follow the active pointer, or
  // the pointer auto-snaps to a fresh CLI mint) — an inner focus is only
  // meaningful against the outer cycle that spawned it.
  const [innerFocus, setInnerFocus] = useState<InnerFocus | null>(null);
  // The `?campaign=&cycle=` deep-link is read in a mount effect rather than
  // a useState initializer so the static-export HTML and the first client
  // render agree (no hydration mismatch).
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
      const inner = urlInner(deepLink);
      if (inner) setInnerFocus(inner);
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
        // unauthed and the loop stops instead of storming. Other failures
        // (incl. the 404 "no active session" steady state) just set the error.
        onAuthError(err);
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
          setPinned(null);
          setInnerFocus(null); // a fresh outer mint invalidates any inner focus
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
    // Inner params ride only while an inner loop is focused (which implies pinned).
    const wantInnerCampaign = innerFocus?.innerCampaignId ?? null;
    const wantInnerCycle = innerFocus?.innerCycleId ?? null;
    if (
      params.get("campaign") === wantCampaign &&
      params.get("cycle") === wantCycle &&
      params.get("inner_campaign") === wantInnerCampaign &&
      params.get("inner_cycle") === wantInnerCycle
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
    if (wantInnerCampaign && wantInnerCycle) {
      params.set("inner_campaign", wantInnerCampaign);
      params.set("inner_cycle", wantInnerCycle);
    } else {
      params.delete("inner_campaign");
      params.delete("inner_cycle");
    }
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [initialized, following, pinned, innerFocus]);

  const selectCycle = useCallback((cid: string, cyid: string) => {
    setFollowing(false);
    setPinned({ campaignId: cid, cycleId: cyid });
    setInnerFocus(null);
  }, []);

  const followActive = useCallback(() => {
    setFollowing(true);
    setPinned(null);
    setInnerFocus(null);
  }, []);

  // Drill into one inner loop of a running L4 outer cycle: pin the outer as the
  // viewed unit (chat + selection stay on it) and re-root the dashboard to the
  // inner cycle. Picking an inner unit is an explicit pin, like selectCycle.
  const selectInner = useCallback((focus: InnerFocus) => {
    setFollowing(false);
    setPinned({ campaignId: focus.outerCampaignId, cycleId: focus.outerCycleId });
    setInnerFocus(focus);
  }, []);

  const clearInner = useCallback(() => {
    setInnerFocus(null);
  }, []);

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
    cycleId,
    campaignId,
    datasetName,
    following,
    cycles,
    cyclesLoaded,
    campaignsLoaded: campaignsFilter === lifecycleFilter,
    cyclesError,
    campaigns,
    activeError,
    lifecycleFilter,
    setLifecycleFilter: selectLifecycle,
    selectCycle,
    followActive,
    innerFocus,
    selectInner,
    clearInner,
  };
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
