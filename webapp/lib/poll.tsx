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
import { reportIncident } from "@/lib/diagnostics";
import { dashboardRead, isWarming, rayRead } from "./api";
import { encodeCyclePath, type CyclePath } from "./ids";
import { useAuthGate } from "./auth-context";
import type { LiveCandidate, RayItem, ServedDashboard } from "./api/types";
import { shownData, useRead } from "./hooks/useRead";
import { invalidateReads, readThrough } from "./read-cache";
import { useWorkspace } from "./workspace";

// The CONNECTION's state, never the run's — that is the served `dash.producer`.
export type StatusKind = "connected" | "offline" | "gone";

export type DashboardSnapshot = ServedDashboard;

// Stable reference: a fresh `[]` per poll drives the candidates card into a setState loop.
const NO_ROWS: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveCandidates(dash: DashboardSnapshot | null): LiveCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

export function liveCandidate(dash: DashboardSnapshot | null, label: string): LiveCandidate | null {
  if (!label) return null;
  return liveCandidates(dash).find((c) => c.reading.arm.label === label) ?? null;
}

export function isMeasuring(dash: DashboardSnapshot | null): boolean {
  const cr = dash?.current_round;
  return cr?.measurement_node != null && cr.active_node === cr.measurement_node;
}

// The served `dash.candidate` is a label, and goes stale between rounds.
export function measuringLabel(dash: DashboardSnapshot | null): string | null {
  if (!dash || !isMeasuring(dash)) return null;
  return dash.candidate || null;
}

export interface CycleStreamState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  isLive: boolean;
}

const CONNECTED = { status: "connected", statusText: "", statusHint: "" } as const;

const OFFLINE = {
  status: "offline",
  statusText: "PromptPotter API unreachable",
  statusHint: "Reconnecting every 5 s — check the server is running.",
} as const;

const GONE = {
  dash: null,
  status: "gone",
  statusText: "This campaign no longer exists",
  statusHint: "It was deleted, or its store was reset. Returning to the active run.",
} as const;

function opening(hasUnit: boolean): Omit<CycleStreamState, "isLive"> {
  return {
    dash: null,
    status: "offline",
    statusText: hasUnit ? "Switching to active campaign…" : "No active campaign",
    statusHint: hasUnit
      ? ""
      : "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.",
  };
}

const RECONNECT_INTERVAL_MS = 5000;
const LIVE_INTERVAL_MS = 2000;

const RAY_WINDOW = 200;

export interface TimeRayState {
  items: RayItem[];
  loaded: boolean;
  failed: boolean;
  hasMore: boolean;
  loadOlder: () => void;
}

export function roundOf(dash: DashboardSnapshot | null): number | null {
  return dash?.current_round.round ?? null;
}

const CycleStreamContext = createContext<CycleStreamState | null>(null);

export function useCycleStream(): CycleStreamState {
  const v = useContext(CycleStreamContext);
  if (!v) {
    throw new Error("useCycleStream must be called inside <CycleStreamProvider>");
  }
  return v;
}

const StreamedAddressContext = createContext<string | null>(null);

export function useDashboardAt(path: CyclePath | null): DashboardSnapshot | null {
  const streamed = useContext(StreamedAddressContext);
  const { dash } = useCycleStream();
  return path !== null && streamed !== null && encodeCyclePath(path) === streamed ? dash : null;
}

// Its own context: sharing `dash`'s would re-render every memoized chart when only the ray moved.
const TimeRayContext = createContext<TimeRayState | null>(null);

export function useTimeRay(): TimeRayState {
  const v = useContext(TimeRayContext);
  if (!v) {
    throw new Error("useTimeRay must be called inside <CycleStreamProvider>");
  }
  return v;
}

function useDashboard(path: CyclePath | null, unit: string | null, intervalMs: number) {
  const { at, reportAddressGone } = useWorkspace();
  const read = useRead(path ? dashboardRead(path, at) : null, {
    auth: true,
    intervalMs,
    onGone: () => {
      if (unit !== null) reportAddressGone(unit);
    },
  });

  const body = shownData(read);
  const landed = body && !isWarming(body) ? body : null;
  // The last snapshot this unit showed, so moving the viewed moment does not blank the page.
  const [shown, setShown] = useState<{ unit: string | null; dash: DashboardSnapshot | null }>({
    unit,
    dash: null,
  });
  if (shown.unit !== unit || (body !== null && shown.dash !== landed)) {
    setShown({ unit, dash: landed });
  }

  const state = useMemo((): Omit<CycleStreamState, "isLive"> => {
    if (read.status === "ready") return { dash: landed, ...CONNECTED };
    if (read.status === "failed") {
      if (read.failure.kind === "gone") return GONE;
      // `dash` stays, so a client blip cannot read an in-flight cycle as gone.
      return { dash: landed ?? (shown.unit === unit ? shown.dash : null), ...OFFLINE };
    }
    const kept = shown.unit === unit ? shown.dash : null;
    return kept ? { dash: kept, ...CONNECTED } : opening(unit !== null);
  }, [read, landed, shown, unit]);

  return { state, replaying: at !== null };
}

const NO_ITEMS: RayItem[] = [];

function useRay(path: CyclePath | null, unit: string | null, intervalMs: number): TimeRayState {
  const { onAuthError } = useAuthGate();
  const head = useRead(path ? rayRead(path, RAY_WINDOW) : null, { auth: true, intervalMs });
  const headWindow = shownData(head);

  const [paged, setPaged] = useState<{ unit: string | null; items: RayItem[]; cursor: string | null }>(
    { unit, items: [], cursor: null },
  );
  if (paged.unit !== unit) setPaged({ unit, items: [], cursor: null });
  const older = paged.unit === unit ? paged.items : NO_ITEMS;
  // A fresh head window's cursor names a boundary already paged past.
  const cursor = older.length > 0 ? paged.cursor : (headWindow?.cursor_prev ?? null);

  const loadingRef = useRef(false);
  const loadOlder = useCallback(() => {
    if (!path || !cursor || loadingRef.current) return;
    loadingRef.current = true;
    const page = rayRead(path, RAY_WINDOW, cursor);
    void readThrough(page.id, page.load, new AbortController().signal)
      .then((res) =>
        setPaged((prev) =>
          prev.unit === unit
            ? { unit, items: [...res.items, ...prev.items], cursor: res.cursor_prev }
            : prev,
        ),
      )
      .catch((e: unknown) => {
        onAuthError(e);
        reportIncident(e, { surface: "ray", address: page.id });
      })
      .finally(() => {
        loadingRef.current = false;
      });
  }, [path, unit, cursor, onAuthError]);

  const headItems = headWindow?.items;
  const items = useMemo(() => [...older, ...(headItems ?? [])], [older, headItems]);
  const loaded = head.status === "ready" || head.status === "failed";
  const failed = head.status === "failed";

  return useMemo<TimeRayState>(
    () => ({ items, loaded, failed, hasMore: cursor !== null, loadOlder }),
    [items, loaded, failed, cursor, loadOlder],
  );
}

export function CycleStreamProvider({
  path,
  children,
}: {
  path: CyclePath | null;
  children: ReactNode;
}) {
  const unit = path ? encodeCyclePath(path) : null;
  const [slow, setSlow] = useState(false);
  const intervalMs = slow ? RECONNECT_INTERVAL_MS : LIVE_INTERVAL_MS;
  const { state, replaying } = useDashboard(path, unit, intervalMs);
  // A replayed moment polls on the slow beat too: a fold at a past offset is immutable.
  const wantSlow = state.status === "offline" || replaying;
  if (wantSlow !== slow) setSlow(wantSlow);
  const ray = useRay(path, unit, intervalMs);

  // `run_phase` also rides `/cycles` and `/tree`, so a change re-asks every live read.
  const phase = state.dash?.run_phase ?? null;
  const seenRef = useRef<{ unit: string | null; phase: string | null }>({ unit: null, phase: null });
  useEffect(() => {
    if (phase === null) return;
    const seen = seenRef.current;
    seenRef.current = { unit, phase };
    if (seen.unit === unit && seen.phase !== null && seen.phase !== phase) invalidateReads();
  }, [unit, phase]);

  const stream = useMemo<CycleStreamState>(
    () => ({
      ...state,
      isLive: !replaying && state.status === "connected" && state.dash?.producer.appending === true,
    }),
    [state, replaying],
  );

  return (
    <StreamedAddressContext.Provider value={unit}>
      <CycleStreamContext.Provider value={stream}>
        <TimeRayContext.Provider value={ray}>{children}</TimeRayContext.Provider>
      </CycleStreamContext.Provider>
    </StreamedAddressContext.Provider>
  );
}
