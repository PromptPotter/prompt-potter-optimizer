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
import { DASHBOARD_LIVE_POLL_MS, DASHBOARD_RECONNECT_POLL_MS } from "@/lib/cadence";
import { phaseIs } from "@/lib/derivations";
import { dashboardRead, isWarming, stepsRead } from "./api";
import { encodeCyclePath, type CyclePath } from "./ids";
import type { HierarchyStep, LiveCandidate, ServedDashboard } from "./api/types";
import type { Said } from "./api/types.generated";
import { shownData, useRead, type ReadFailure } from "./hooks/useRead";
import { invalidateReads } from "./read-cache";
import { useWorkspace } from "./workspace";

// The CONNECTION's state, never the run's — that is the served `dash.producer`. `lost`: the read
// failed, a transient failure being the server unreachable; `gone`: the address no longer exists.
export type Connection =
  | { kind: "connected" }
  | { kind: "lost"; failure: ReadFailure }
  | { kind: "gone" };

// Stable reference: a fresh `[]` per poll drives the candidates card into a setState loop.
const NO_ROWS: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveCandidates(dash: ServedDashboard | null): LiveCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

export function liveCandidate(
  dash: ServedDashboard | null,
  key: string | null,
): LiveCandidate | null {
  if (!key) return null;
  return liveCandidates(dash).find((c) => c.reading.arm.key === key) ?? null;
}

interface CycleStreamState {
  dash: ServedDashboard | null;
  connection: Connection;
  isLive: boolean;
}

const CONNECTED: Connection = { kind: "connected" };
const GONE: Connection = { kind: "gone" };

export const DASHBOARD_PENDING = "Waiting for this run’s dashboard…";

// The connection's own row: the one `Said` the client makes. Only a transient failure is the
// server unreachable; an answered refusal says its own sentence.
export function connectionSaid(connection: Connection): Said | null {
  if (connection.kind === "connected") return null;
  if (connection.kind === "gone") {
    return {
      key: "address_gone",
      tone: "attention",
      label: "This campaign no longer exists",
      value: null,
      sub: null,
      detail: "It was deleted, or its store was reset. Returning to the active run.",
    };
  }
  const offline = connection.failure.kind === "transient";
  return {
    key: "connection_lost",
    tone: "danger",
    label: offline ? "PromptPotter API unreachable" : connection.failure.message,
    value: null,
    sub: null,
    detail: offline
      ? `Reconnecting every ${DASHBOARD_RECONNECT_POLL_MS / 1000} s — check the server is running.`
      : null,
  };
}

const RAY_WINDOW = 200;

// The head window's read: `failed` carries the failure, never a flag.
export type RayRead =
  | { status: "loading" }
  | { status: "ready" }
  | { status: "failed"; failure: ReadFailure };

interface TimeRayState {
  steps: HierarchyStep[];
  head: RayRead;
  // The last "earlier" page asked, null while none failed.
  olderFailure: ReadFailure | null;
  hasMore: boolean;
  loadOlder: () => void;
}

export function roundOf(dash: ServedDashboard | null): number | null {
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

// `streamed`: `path` is the address this provider polls, so `dash` is its dashboard (null until it
// lands); off the stream `dash` is null and a caller needing one reads its own.
export function useDashboardAt(path: CyclePath | null): {
  streamed: boolean;
  dash: ServedDashboard | null;
} {
  const unit = useContext(StreamedAddressContext);
  const { dash } = useCycleStream();
  const streamed = path !== null && unit !== null && encodeCyclePath(path) === unit;
  return { streamed, dash: streamed ? dash : null };
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
  const [shown, setShown] = useState<{ unit: string | null; dash: ServedDashboard | null }>({
    unit,
    dash: null,
  });
  if (shown.unit !== unit || (body !== null && shown.dash !== landed)) {
    setShown({ unit, dash: landed });
  }

  const state = useMemo((): Omit<CycleStreamState, "isLive"> => {
    const kept = shown.unit === unit ? shown.dash : null;
    if (read.status === "ready") return { dash: landed, connection: CONNECTED };
    if (read.status === "failed") {
      if (read.failure.kind === "gone") return { dash: null, connection: GONE };
      // `dash` stays, so a client blip cannot read an in-flight cycle as gone.
      return { dash: landed ?? kept, connection: { kind: "lost", failure: read.failure } };
    }
    return { dash: kept, connection: CONNECTED };
  }, [read, landed, shown, unit]);

  return { state, replaying: at !== null };
}

const NO_STEPS: HierarchyStep[] = [];

interface Paged {
  unit: string | null;
  steps: HierarchyStep[];
  cursor: string | null;
  // The cursor whose page `steps` already holds, so a re-asked page is never prepended twice.
  through: string | null;
}

function useRay(path: CyclePath | null, unit: string | null, intervalMs: number | undefined): TimeRayState {
  const head = useRead(path ? stepsRead(path, RAY_WINDOW) : null, { auth: true, intervalMs });
  const headWindow = shownData(head);

  const fresh: Paged = { unit, steps: [], cursor: null, through: null };
  const [paged, setPaged] = useState<Paged>(fresh);
  const [asked, setAsked] = useState<{ unit: string | null; cursor: string } | null>(null);
  if (paged.unit !== unit) {
    setPaged(fresh);
    setAsked(null);
  }
  const own = paged.unit === unit ? paged : fresh;
  const askedCursor = asked !== null && asked.unit === unit ? asked.cursor : null;
  // A fresh head window's cursor names a boundary already paged past.
  const cursor = own.steps.length > 0 ? own.cursor : (headWindow?.cursor_prev ?? null);

  const page = useRead(path && askedCursor ? stepsRead(path, RAY_WINDOW, askedCursor) : null, {
    auth: true,
  });
  if (page.status === "ready" && askedCursor !== null && own.through !== askedCursor) {
    setPaged({
      unit,
      steps: [...page.data.steps, ...own.steps],
      cursor: page.data.cursor_prev,
      through: askedCursor,
    });
  }
  // One value guards the claim and its fetch: a page is in the air while its cursor is unpaged.
  const inFlight = askedCursor !== null && own.through !== askedCursor && page.status !== "failed";
  const pageFailed = page.status === "failed";
  const loadOlder = useCallback(() => {
    if (cursor === null || inFlight) return;
    // The same page asked again re-asks the read; a new cursor names a new one.
    if (pageFailed && askedCursor === cursor) invalidateReads("steps");
    else setAsked({ unit, cursor });
  }, [cursor, inFlight, unit, pageFailed, askedCursor]);

  const older = own.steps.length > 0 ? own.steps : NO_STEPS;
  const headSteps = headWindow?.steps;
  const steps = useMemo(() => [...older, ...(headSteps ?? [])], [older, headSteps]);
  const headRead = useMemo<RayRead>(
    () =>
      head.status === "failed"
        ? { status: "failed", failure: head.failure }
        : head.status === "ready"
          ? { status: "ready" }
          : { status: "loading" },
    [head],
  );
  const olderFailure = page.status === "failed" ? page.failure : null;

  return useMemo<TimeRayState>(
    () => ({ steps, head: headRead, olderFailure, hasMore: cursor !== null, loadOlder }),
    [steps, headRead, olderFailure, cursor, loadOlder],
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
  const intervalMs = slow ? DASHBOARD_RECONNECT_POLL_MS : DASHBOARD_LIVE_POLL_MS;
  const { state, replaying } = useDashboard(path, unit, intervalMs);
  // A replayed moment polls on the slow beat too: a fold at a past offset is immutable.
  const wantSlow = state.connection.kind === "lost" || replaying;
  if (wantSlow !== slow) setSlow(wantSlow);
  // `run_phase` also rides `/hierarchy` and `/hierarchy/{address}`, so a change re-asks every live read.
  const phase = state.dash?.run_phase ?? null;
  // A settled run's steps move only on a resume, whose phase change re-asks them below.
  const ray = useRay(path, unit, phaseIs(phase, "settled") ? undefined : intervalMs);
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
      isLive:
        !replaying && state.connection.kind === "connected" && state.dash?.producer.appending === true,
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
