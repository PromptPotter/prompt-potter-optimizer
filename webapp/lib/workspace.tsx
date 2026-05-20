"use client";
// Single source of truth for campaign identity. One provider polls the
// server's active pointer (`/active`) and the cycle list (`/cycles`)
// together; every surface — DashboardPane, CyclePicker, Sidebar,
// ComparePane — subscribes here via `useWorkspace()` instead of fetching
// those endpoints on its own. Replaces the prior scatter of independent
// resolvers that drifted out of sync the moment the CLI minted a cycle.
//
// `following` is the explicit follow-vs-pin state. While following,
// `cycleId` tracks the server's `activeCycleId` in lockstep. Picking a
// cycle pins it (`following=false`); `followActive()` resumes following.
// The URL `?cycle=` param is written ONLY while pinned and stripped while
// following — so the app's own writeback can never re-read as a pin on
// the next reload (the bug that used to freeze the dashboard after one
// refresh).

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { fetchActive, fetchCycles, type CycleListEntry } from "./api";

export interface WorkspaceState {
  sessionId: string | null;
  activeCycleId: string | null; // server pointer (active_session.json)
  cycleId: string | null; // the cycle being VIEWED
  following: boolean; // cycleId tracks activeCycleId
  cycles: CycleListEntry[];
  cyclesLoaded: boolean; // first /cycles poll has resolved (success or fail)
  cyclesError: string | null;
  activeError: string | null;
  selectCycle: (id: string) => void; // user pin → following=false
  followActive: () => void; // un-pin → snap back to activeCycleId
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function useWorkspace(): WorkspaceState {
  const v = useContext(WorkspaceContext);
  if (!v) {
    throw new Error("useWorkspace must be called inside <WorkspaceProvider>");
  }
  return v;
}

function urlCycleParam(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("cycle");
}

export function WorkspaceProvider({
  intervalMs = 3000,
  children,
}: {
  intervalMs?: number;
  children: ReactNode;
}) {
  const [pinnedCycleId, setPinnedCycleId] = useState<string | null>(null);
  const [following, setFollowing] = useState(true);
  // The `?cycle=` deep-link is read in a mount effect rather than a
  // useState initializer so the static-export HTML and the first client
  // render agree (no hydration mismatch). Until it resolves, the URL
  // writeback is suppressed.
  const [initialized, setInitialized] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [activeCycleId, setActiveCycleId] = useState<string | null>(null);
  const [cycles, setCycles] = useState<CycleListEntry[]>([]);
  const [cyclesLoaded, setCyclesLoaded] = useState(false);
  const [cyclesError, setCyclesError] = useState<string | null>(null);
  const [activeError, setActiveError] = useState<string | null>(null);

  // Mount: honour a `?cycle=` deep-link as an explicit pin.
  useEffect(() => {
    const deepLink = urlCycleParam();
    if (deepLink) {
      setPinnedCycleId(deepLink);
      setFollowing(false);
    }
    setInitialized(true);
  }, []);

  // One poll loop — active pointer and cycle list move together so the
  // list and the `●` pointer can never disagree.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const [activeRes, cyclesRes] = await Promise.allSettled([
        fetchActive(),
        fetchCycles(),
      ]);
      if (cancelled) return;
      if (activeRes.status === "fulfilled") {
        setSessionId(activeRes.value.session_id || null);
        setActiveCycleId(activeRes.value.cycle_id || null);
        setActiveError(null);
      } else {
        setActiveError(
          (activeRes.reason as Error)?.message ?? "active session unavailable",
        );
      }
      if (cyclesRes.status === "fulfilled") {
        setCycles(cyclesRes.value.cycles);
        // `/cycles` also carries the active pointer — use it as a fallback
        // only when `/active` itself failed this tick.
        if (activeRes.status !== "fulfilled" && cyclesRes.value.active_cycle_id) {
          setActiveCycleId(cyclesRes.value.active_cycle_id);
        }
        setCyclesError(null);
      } else {
        setCyclesError(
          (cyclesRes.reason as Error)?.message ?? "campaign list unavailable",
        );
      }
      setCyclesLoaded(true);
    };
    void tick();
    const handle = window.setInterval(() => void tick(), intervalMs);
    const onFocus = () => void tick();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(handle);
      window.removeEventListener("focus", onFocus);
    };
  }, [intervalMs]);

  // The viewed cycle: the server pointer while following, else the pin.
  const cycleId = following ? activeCycleId : pinnedCycleId;

  // URL contract: `?cycle=X` present ⇔ pinned to X. Written only while
  // pinned, stripped while following.
  useEffect(() => {
    if (!initialized || typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const want = following ? null : pinnedCycleId;
    if (params.get("cycle") === want) return;
    if (want) params.set("cycle", want);
    else params.delete("cycle");
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }, [initialized, following, pinnedCycleId]);

  const selectCycle = useCallback((id: string) => {
    setFollowing(false);
    setPinnedCycleId(id);
  }, []);

  const followActive = useCallback(() => {
    setFollowing(true);
    setPinnedCycleId(null);
  }, []);

  const value: WorkspaceState = {
    sessionId,
    activeCycleId,
    cycleId,
    following,
    cycles,
    cyclesLoaded,
    cyclesError,
    activeError,
    selectCycle,
    followActive,
  };
  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}
