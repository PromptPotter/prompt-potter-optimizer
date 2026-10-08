"use client";
// The live-stream store: one timer polls the dashboard (a fold of the ledger) and `/ray` (its
// chronology) — on separate cadences they drift and report a disagreement that is not on disk.
// Dashboard revalidates on `Last-Modified` (304), the ray head on an ETag; `?descend=` serves any
// depth through one fetch. `at` is the viewed moment, a leaf-ledger offset; `null` is the head.
// A BARE `llm_call_progress` ray tick is counted, never rendered; the server must keep sending it
// or every heartbeated backend query grows a spurious gap marker (`format.ts::fmtGap`).

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
import { failureKind, fetchDashboardByPath, fetchTimeRay } from "./api";
import { encodeCyclePath, pathLeaf, type CyclePath } from "./ids";
import { useAuthGate } from "./auth-context";
import { ageTextSeconds } from "./format";
import type { LiveCandidate, LiveDashboardState, RayItem } from "./api/types";
import { RUN_FRESH_S, type RunPhase } from "./api/types.generated";
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation, useRevalidation } from "./revalidate";
import { hasLiveProducer } from "./run-phase";
import { useWorkspace } from "./workspace";

// `gone` is NOT a flavour of `offline`: "didn't answer" means retry, "answered: no longer exists"
// means stop (frontend-surface-contract.md § I7).
export type StatusKind = "live" | "stale" | "offline" | "gone";

export type DashboardSnapshot = LiveDashboardState;

// Hand-written: `live_dashboard/state.py::warming_payload` is a bare dict with no model to
// generate from.
interface WarmingSnapshot {
  warming_up: true;
  campaign_id: string;
  cycle_id: string;
  // Separates "no snapshot YET" from "EVER": a producer that died during init reads
  // `detached`/`terminal` here while still having no dashboard.
  run_phase: RunPhase;
}

function isWarming(d: unknown): d is WarmingSnapshot {
  return !!d && typeof d === "object" && (d as WarmingSnapshot).warming_up === true;
}

// A stable empty reference: a fresh `[]` per poll churns the candidates card's Set chain into an
// unbounded setState loop.
const NO_ROWS: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveCandidates(dash: DashboardSnapshot | null): LiveCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

// Joins on `label`: a live row has no lineage id until `candidate_scored` stamps one.
export function liveCandidate(dash: DashboardSnapshot | null, label: string): LiveCandidate | null {
  if (!label) return null;
  return liveCandidates(dash).find((c) => c.label === label) ?? null;
}

// Whether the round's measurement node is the one working — a node the manifest names, never a
// literal the browser holds.
export function isMeasuring(dash: DashboardSnapshot | null): boolean {
  const cr = dash?.current_round;
  return cr?.measurement_node != null && cr.active_node === cr.measurement_node;
}

export interface CycleStreamState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  termKey: string;
  // Running AND fresh AND showing the head — the one gate for every transient indicator.
  // Composed once, in `useCycleStreamSource`; consumers never re-derive it.
  isLive: boolean;
  at: number | null;
}

// What a unit shows before its first read lands — or, with no unit in view, in place of one.
function openingState(hasUnit: boolean): CycleStreamState {
  return {
    dash: null,
    status: "offline",
    statusText: hasUnit ? "Switching to active campaign…" : "No active campaign",
    statusHint: hasUnit
      ? ""
      : "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.",
    termKey: "status_offline",
    isLive: false,
    at: null,
  };
}

// What the dashboard tick remembers between polls of ONE unit. Stamped with that unit's key and
// replaced on a mismatch, so nothing has to clear it when the view moves.
interface UnitScratch {
  key: string | null;
  stampMismatch: number;
  gone: number;
  // The same `run_phase` rides `/cycles` (10 s) and `/tree` (5 s); a change seen here re-ticks
  // both so three surfaces do not sit apart on one transition.
  lastPhase: string | null;
}

// A `Last-Modified` answers for one unit at one moment: the same file mtime reads differently at
// "the head" and at "offset N", so a validator replayed across either would 304 the fold away.
interface HeldValidator {
  key: string | null;
  at: number | null;
  value: string | null;
}

// The address the hook is rendered for, as the ticks and `loadOlder` read it between renders.
interface LiveAddress {
  path: CyclePath | null;
  key: string | null;
  at: number | null;
}

// Rides out a one-tick re-instantiation during `new`.
const STAMP_MISMATCH_LIMIT = 3;

// One 404 is a mint race; this verdict unpins the operator's view.
const GONE_CONFIRM_LIMIT = 3;

const RECONNECT_INTERVAL_MS = 5000;

const RAY_WINDOW = 200;

// Module-scoped: `usePoll` reads it every tick and must not see a new array per render.
const POLL_KEYS = (): readonly string[] => ["dash", "ray"];

interface RayWindows {
  older: RayItem[];
  head: RayItem[];
  // null at the family's beginning.
  cursor: string | null;
  loaded: boolean;
  failed: boolean;
}

const EMPTY_RAY: RayWindows = {
  older: [],
  head: [],
  cursor: null,
  loaded: false,
  failed: false,
};

export interface TimeRayState {
  items: RayItem[];
  loaded: boolean;
  failed: boolean;
  hasMore: boolean;
  loadOlder: () => void;
  /** A derivation cannot call `Date.now()` and a 304 re-renders nothing, so without this tick
   *  the head's "no progress for Xm" reading freezes. */
  nowMs: number;
  /** An offset in THIS course's ledger; a fork's or inner run's step is an address, not a moment
   *  — those ledgers have their own offsets (`infrastructure/ledger.py::iter`). */
  setAt: (offset: number | null) => void;
}

export interface BucketResult {
  status: StatusKind;
  statusText: string;
  statusHint: string;
  termKey: string;
}

// The round in flight. `current_round.round` IS the writer's `state.round`, so it is the one read.
export function roundOf(dash: DashboardSnapshot | null): number | null {
  return dash?.current_round.round ?? null;
}

function wallclockAgeS(iso: string | null | undefined): number | null {
  const wall = Date.parse(iso || "");
  return Number.isFinite(wall) ? (Date.now() - wall) / 1000 : null;
}

export function ageBucket(ageS: number | null): BucketResult {
  if (ageS == null) {
    return {
      status: "stale",
      statusText: "No wallclock on dashboard",
      statusHint: "Optimizer may not have started yet",
      termKey: "status_nowall",
    };
  }
  // The server's `running`/`detached` window, so the banner and `run_phase` cannot disagree.
  // The 5 m below is this banner's own.
  if (ageS < RUN_FRESH_S) {
    return {
      status: "live",
      statusText: `Live · last write ${ageS.toFixed(0)}s ago`,
      statusHint: "",
      termKey: "status_live",
    };
  }
  if (ageS < 5 * 60) {
    return {
      status: "stale",
      statusText: `Idle · last write ${ageS.toFixed(0)}s ago`,
      statusHint: "Round between phases or paused",
      termKey: "status_idle",
    };
  }
  return {
    status: "stale",
    statusText: `UPDATED · ${ageTextSeconds(ageS)}`,
    statusHint: "No live optimizer — viewing a frozen unit",
    termKey: "status_snapshot",
  };
}

const CycleStreamContext = createContext<CycleStreamState | null>(null);

export function useCycleStream(): CycleStreamState {
  const v = useContext(CycleStreamContext);
  if (!v) {
    throw new Error("useCycleStream must be called inside <CycleStreamProvider>");
  }
  return v;
}

// Its own context: the ray changes identity on a different beat from `dash`, and one value would
// re-render every memoized chart on a poll that only moved the strip.
const TimeRayContext = createContext<TimeRayState | null>(null);

export function useTimeRay(): TimeRayState {
  const v = useContext(TimeRayContext);
  if (!v) {
    throw new Error("useTimeRay must be called inside <CycleStreamProvider>");
  }
  return v;
}

function useCycleStreamSource(
  path: CyclePath | null,
  intervalMs: number,
): { stream: CycleStreamState; ray: TimeRayState } {
  // The whole encoded path is the identity: a cycle_id is unique only within its campaign, an
  // inner one only within its parent's sandbox.
  const unitKey = path ? encodeCyclePath(path) : null;
  const [state, setState] = useState<CycleStreamState>(() => openingState(unitKey !== null));
  const [ray, setRay] = useState<RayWindows>(EMPTY_RAY);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [at, setAtState] = useState<number | null>(null);
  const { authed, onAuthError } = useAuthGate();
  // Identity-stable by construction (workspace.tsx) — the tick must not re-arm on it.
  const { reportAddressGone } = useWorkspace();
  const [revalCount, setRevalCount] = useState(0);

  const [prevKey, setPrevKey] = useState(unitKey);
  if (unitKey !== prevKey) {
    setPrevKey(unitKey);
    // A moment is an offset into ONE cycle's ledger and means nothing in another.
    setAtState(null);
    setState(openingState(unitKey !== null));
    setRay(EMPTY_RAY);
    setRevalCount((c) => c + 1);
  }

  // Everything a tick keeps between polls is STAMPED with the address it describes and read back
  // only on a match, so a late response cannot poison the next unit's validator.
  const liveRef = useRef<LiveAddress>({ path: null, key: null, at: null });
  // Declared before `usePoll`, whose effects fire the ticks that read it.
  useEffect(() => {
    liveRef.current = { path, key: unitKey, at };
  });
  const scratchRef = useRef<UnitScratch>({ key: null, stampMismatch: 0, gone: 0, lastPhase: null });
  const validatorRef = useRef<HeldValidator>({ key: null, at: null, value: null });
  const rayEtagRef = useRef<{ key: string | null; etag: string | null }>({
    key: null,
    etag: null,
  });
  const loadingOlderRef = useRef(false);

  const setAt = useCallback((offset: number | null) => {
    setAtState(offset);
    setRevalCount((c) => c + 1);
  }, []);

  const tickDash = async (signal: AbortSignal) => {
    const { path: p, key, at: moment } = liveRef.current;
    if (!p || key === null) return;
    const { cycleId: id, campaignId: cmp } = pathLeaf(p);
    if (scratchRef.current.key !== key) {
      scratchRef.current = { key, stampMismatch: 0, gone: 0, lastPhase: null };
    }
    const unit = scratchRef.current;
    const held = validatorRef.current;
    const known = held.key === key && held.at === moment ? held.value : null;
    // The view moved while this read was in flight: its answer describes another address.
    const superseded = () =>
      signal.aborted || liveRef.current.key !== key || liveRef.current.at !== moment;
    try {
      const resp = await fetchDashboardByPath(p, known, signal, moment);
      if (superseded()) return;
      unit.gone = 0;

      if (resp.kind === "not_modified") {
        setState((prev) => {
          const ageS = wallclockAgeS(prev.dash?.wallclock_serialized_at);
          const bucket = ageBucket(ageS);
          if (prev.termKey === bucket.termKey) return prev;
          return {
            ...prev,
            status: bucket.status,
            statusText: bucket.statusText,
            statusHint: bucket.statusHint,
            termKey: bucket.termKey,
            isLive: bucket.status === "live" && prev.dash?.run_phase === "running",
          };
        });
        return;
      }

      if (resp.validator) validatorRef.current = { key, at: moment, value: resp.validator };

      if (isWarming(resp.data)) {
        unit.stampMismatch = 0;
        const stillComing = hasLiveProducer(resp.data.run_phase);
        setState((prev) => ({
          ...prev,
          dash: null,
          status: "stale",
          statusText: stillComing ? "Origin running" : "No snapshot was ever written",
          statusHint: stillComing
            ? "First snapshot lands when origin completes — campaign is initialising."
            : "The run stopped before its first snapshot. Nothing to show for this cycle.",
          termKey: "status_warming_up",
          isLive: false,
        }));
        return;
      }

      const dash = resp.data as unknown as DashboardSnapshot;

      // Drop a payload self-stamped for another unit — a late response from the prior cycle, or a
      // `new` mid re-instantiation.
      if (dash.campaign_id !== cmp || dash.cycle_id !== id) {
        const reported = `(${dash.campaign_id}, ${dash.cycle_id})`;
        const expected = `(${cmp}, ${id})`;
        console.debug(
          `[cycle-stream] dropped dashboard payload — stamp ${reported} != unit ${expected}`,
        );
        unit.stampMismatch += 1;
        if (unit.stampMismatch >= STAMP_MISMATCH_LIMIT) {
          setState((prev) => ({
            ...prev,
            status: "stale",
            statusText: "Dashboard identity mismatch",
            statusHint:
              `dashboard.json reports ${reported} but this view expects ${expected} — ` +
              "the optimizer may be re-instantiating, or this unit's session " +
              "never wrote a dashboard.",
            termKey: "status_stamp_mismatch",
            isLive: false,
          }));
        }
        return;
      }
      unit.stampMismatch = 0;
      if (dash.run_phase !== unit.lastPhase) {
        const first = unit.lastPhase === null;
        unit.lastPhase = dash.run_phase;
        // The first observation is this unit's opening read, not a transition.
        if (!first) bumpRevalidation();
      }
      const ageS = wallclockAgeS(dash.wallclock_serialized_at);
      const bucket = ageBucket(ageS);
      setState((prev) => ({
        ...prev,
        dash,
        status: bucket.status,
        statusText: bucket.statusText,
        statusHint: bucket.statusHint,
        termKey: bucket.termKey,
        isLive: bucket.status === "live" && dash.run_phase === "running",
      }));
    } catch (e) {
      if (superseded()) return;
      onAuthError(e);
      reportIncident(e, { surface: "dashboard", address: key });

      // The route answers `warming_up` at 200 for a cycle with no dashboard yet, so a 404 here
      // means the cycle dir itself is gone.
      if (failureKind(e) === "gone") {
        unit.gone += 1;
        if (unit.gone >= GONE_CONFIRM_LIMIT) reportAddressGone(key);
        setState((prev) => ({
          ...prev,
          // Every number in the kept snapshot would describe a run no longer on disk.
          dash: null,
          status: "gone",
          statusText: "This campaign no longer exists",
          statusHint:
            "It was deleted, or its store was reset. Returning to the active run.",
          termKey: "status_gone",
          isLive: false,
        }));
        return;
      }
      unit.gone = 0;
      setState((prev) => ({
        ...prev,
        status: "offline",
        statusText: "PromptPotter API unreachable",
        statusHint: "Reconnecting every 5 s — check the server is running.",
        termKey: "status_offline",
        // `dash.run_phase` stays untouched, so a client blip cannot read an in-flight cycle as gone.
        isLive: false,
      }));
    }
  };

  const tickRay = async (signal: AbortSignal) => {
    const { path: p, key } = liveRef.current;
    if (!p) return;
    // Replaying a stale ETag would 304 into an empty window.
    const known = rayEtagRef.current.key === key ? rayEtagRef.current.etag : null;
    try {
      const res = await fetchTimeRay(p, { limit: RAY_WINDOW }, known, signal);
      if (signal.aborted || liveRef.current.key !== key) return;
      if (res.kind === "not_modified") {
        setRay((prev) => (prev.loaded ? prev : { ...prev, loaded: true }));
        return;
      }
      rayEtagRef.current = { key, etag: res.validator };
      setRay((prev) => ({
        // A fresh head window's cursor describes a boundary already paged past, so the oldest
        // loaded window's cursor stands.
        older: prev.older,
        head: res.data.items,
        cursor: prev.older.length > 0 ? prev.cursor : res.data.cursor_prev,
        loaded: true,
        failed: false,
      }));
    } catch (e) {
      if (signal.aborted) return;
      onAuthError(e);
      reportIncident(e, { surface: "ray", address: key });
      setRay((prev) => ({ ...prev, loaded: true, failed: true }));
    }
  };

  const tick = (signal: AbortSignal, key: string): Promise<void> => {
    if (key === "ray") return tickRay(signal);
    setNowMs(Date.now());
    return tickDash(signal);
  };

  // Replaying drops to the 5 s cadence too: a fold at a past offset is immutable, so re-folding
  // it at 2 s buys only server work.
  const effectiveInterval =
    state.status === "offline" || at !== null ? RECONNECT_INTERVAL_MS : intervalMs;
  usePoll(tick, {
    intervalMs: effectiveInterval,
    keys: POLL_KEYS,
    // A confirmed `gone` stops the loop; the unit-key guard resets `status`, so the next real
    // address re-arms it.
    enabled: !!path && authed && state.status !== "gone",
    revalidateOn: revalCount + useRevalidation(),
    tickOnFocus: true,
  });

  const cursor = ray.cursor;
  const loadOlder = useCallback(() => {
    const { path: p, key } = liveRef.current;
    if (!p || !cursor || loadingOlderRef.current) return;
    loadingOlderRef.current = true;
    void fetchTimeRay(p, { limit: RAY_WINDOW, before: cursor })
      .then((res) => {
        if (res.kind !== "ok" || liveRef.current.key !== key) return;
        setRay((prev) => ({
          ...prev,
          older: [...res.data.items, ...prev.older],
          cursor: res.data.cursor_prev,
        }));
      })
      .catch((e: unknown) => onAuthError(e))
      .finally(() => {
        loadingOlderRef.current = false;
      });
  }, [cursor, onAuthError]);

  const items = useMemo(() => [...ray.older, ...ray.head], [ray.older, ray.head]);

  // A fold stamps `wallclock_serialized_at` when composed, so a replayed moment would read
  // "Live · last write 0s ago" over an hour-old round; replay overrides the age banner.
  const stream = useMemo<CycleStreamState>(() => {
    if (at === null) return { ...state, at };
    return {
      ...state,
      at,
      isLive: false,
      status: "stale",
      statusText: `Replaying · step ${at}`,
      statusHint: "Every panel shows what was true at this step. Pick “now ›” to follow again.",
      termKey: "status_replaying",
    };
  }, [state, at]);
  const rayState = useMemo<TimeRayState>(
    () => ({
      items,
      loaded: ray.loaded,
      failed: ray.failed,
      hasMore: cursor !== null,
      loadOlder,
      nowMs,
      setAt,
    }),
    [items, ray.loaded, ray.failed, cursor, loadOlder, nowMs, setAt],
  );

  return { stream, ray: rayState };
}

// Matched by the workspace's active-pointer poll, so a CLI-minted cycle is followed without lag.
const DASHBOARD_INTERVAL_MS = 2000;

export function CycleStreamProvider({
  path,
  children,
}: {
  path: CyclePath | null;
  children: ReactNode;
}) {
  const { stream, ray } = useCycleStreamSource(path, DASHBOARD_INTERVAL_MS);
  return (
    <CycleStreamContext.Provider value={stream}>
      <TimeRayContext.Provider value={ray}>{children}</TimeRayContext.Provider>
    </CycleStreamContext.Provider>
  );
}
