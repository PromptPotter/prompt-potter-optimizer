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
import type {
  DashboardCandidate,
  DashboardSample,
  LiveDashboardState,
  RayItem,
  ValidationFailure,
} from "./api/types";
import { RUN_FRESH_S } from "./api/types.generated";
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation, useRevalidation } from "./revalidate";
import { hasLiveProducer } from "./run-phase";
import { useWorkspace } from "./workspace";

// `gone` is NOT a flavour of `offline`: "didn't answer" means retry, "answered: no longer exists"
// means stop (frontend-surface-contract.md § I7).
export type StatusKind = "live" | "stale" | "offline" | "gone";

export type DashboardSnapshot = LiveDashboardState;

export interface WarmingSnapshot {
  warming_up: true;
  campaign_id: string;
  cycle_id: string;
  phase_hint: string;
  // Separates "no snapshot YET" from "EVER": a producer that died during init reads
  // `detached`/`terminal` here while still having no dashboard.
  run_phase: string;
}

function isWarming(d: unknown): d is WarmingSnapshot {
  return !!d && typeof d === "object" && (d as WarmingSnapshot).warming_up === true;
}

export interface LiveCandidate {
  idx?: number;
  label?: string;
  samples?: DashboardSample[];
  sample_lines?: string[];
  // The only place a validation rejection's reasons are served; `current_round.candidates`
  // carries just the `invalid` flag, which is mirrored here.
  invalid?: boolean;
  validation_failures?: ValidationFailure[];
  // Numbers ride `current_round.candidates` in the closed-round shape; this half owns the tape.
}

export interface LiveInputCandidate {
  idx?: number;
  label?: string;
  changes_description?: string;
  // Present on settled `round_NNNN.json::candidate_scores[]` rows; absent on in-flight rows.
  candidate_id?: string;
  prompt_fields?: Record<string, unknown>;
  // Server-resolved, config-only effective params (`{node:{param:value}, steps}`), prompt stripped.
  resolved_pipeline_params?: Record<string, unknown> | null;
}

export interface L1ScoreOutput {
  candidates?: LiveCandidate[];
}

interface L1ScoreInput {
  candidates?: LiveInputCandidate[];
}

// A stable empty reference: a fresh `[]` per poll churns the candidates card's Set chain into an
// unbounded setState loop.
const NO_CANDIDATES: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveL1Candidates(dash: DashboardSnapshot | null): LiveCandidate[] {
  const nodes = dash?.current_round.nodes;
  if (!nodes || typeof nodes !== "object") return NO_CANDIDATES;
  const l1 = (nodes as Record<string, { output?: L1ScoreOutput }>).l1_score;
  return l1?.output?.candidates ?? NO_CANDIDATES;
}

const NO_ROWS: DashboardCandidate[] = Object.freeze(
  [] as DashboardCandidate[],
) as DashboardCandidate[];

export function liveCandidates(dash: DashboardSnapshot | null): DashboardCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

const NO_INPUT_CANDIDATES: LiveInputCandidate[] = Object.freeze(
  [] as LiveInputCandidate[],
) as LiveInputCandidate[];

export function liveL1InputCandidates(
  dash: DashboardSnapshot | null,
): LiveInputCandidate[] {
  const nodes = dash?.current_round.nodes;
  if (!nodes || typeof nodes !== "object") return NO_INPUT_CANDIDATES;
  const l1 = (nodes as Record<string, { input?: L1ScoreInput }>).l1_score;
  return l1?.input?.candidates ?? NO_INPUT_CANDIDATES;
}

// Joins on `label`: a live row has no lineage id until `candidate_scored` stamps one.
function matchLiveCandidate<T extends { label?: string }>(
  candidates: readonly T[],
  label: string,
): T | null {
  if (!label) return null;
  return candidates.find((c) => c.label === label) ?? null;
}

export const liveCandidate = (
  dash: DashboardSnapshot | null,
  label: string,
): LiveCandidate | null => matchLiveCandidate(liveL1Candidates(dash), label);

export const liveInputCandidate = (
  dash: DashboardSnapshot | null,
  label: string,
): LiveInputCandidate | null => matchLiveCandidate(liveL1InputCandidates(dash), label);

export interface CycleStreamState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  termKey: string;
  error: string | null;
  // Running AND fresh AND showing the head — the one gate for every transient indicator.
  // Composed once, in `useCycleStreamSource`; consumers never re-derive it.
  isLive: boolean;
  phase: string | null;
  at: number | null;
}

const INITIAL_STATE: CycleStreamState = {
  dash: null,
  status: "offline",
  statusText: "Connecting…",
  statusHint: "",
  termKey: "status_offline",
  error: null,
  isLive: false,
  phase: null,
  at: null,
};

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

// `current_round.round` is authoritative; the fall-through covers re-instantiation before any
// phase has fired.
export function roundOf(dash: DashboardSnapshot | null): number | null {
  const r = dash?.current_round.round ?? dash?.round;
  return typeof r === "number" ? r : null;
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
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  const [ray, setRay] = useState<RayWindows>(EMPTY_RAY);
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [at, setAtState] = useState<number | null>(null);
  const { authed, onAuthError } = useAuthGate();
  // Identity-stable by construction (workspace.tsx) — the tick must not re-arm on it.
  const { reportAddressGone } = useWorkspace();
  const [revalCount, setRevalCount] = useState(0);
  const cycleRef = useRef<string | null>(null);
  const campaignRef = useRef<string | null>(null);
  const stampMismatchRef = useRef(0);
  const goneRef = useRef(0);
  const lastModifiedRef = useRef<string | null>(null);
  // The same `run_phase` rides `/cycles` (10 s) and `/tree` (5 s); a change seen here re-ticks
  // both so three surfaces do not sit apart on one transition.
  const lastPhaseRef = useRef<string | null>(null);
  const pathRef = useRef<CyclePath | null>(null);
  const atRef = useRef<number | null>(null);
  // Stamped with its key rather than cleared, keeping writes out of render (`react-hooks/refs`);
  // replaying a stale ETag would 304 into an empty window.
  const rayEtagRef = useRef<{ key: string | null; etag: string | null }>({
    key: null,
    etag: null,
  });
  const loadingOlderRef = useRef(false);

  // The whole encoded path is the identity: a cycle_id is unique only within its campaign, an
  // inner one only within its parent's sandbox.
  const unitKeyRef = useRef<string | null>(null);
  const unitKey = path ? encodeCyclePath(path) : null;
  if (unitKeyRef.current !== unitKey) {
    unitKeyRef.current = unitKey;
    pathRef.current = path;
    const leaf = path ? pathLeaf(path) : null;
    cycleRef.current = leaf?.cycleId ?? null;
    campaignRef.current = leaf?.campaignId ?? null;
    stampMismatchRef.current = 0;
    goneRef.current = 0;
    lastModifiedRef.current = null;
    lastPhaseRef.current = null;
    // A moment is an offset into ONE cycle's ledger and means nothing in another.
    atRef.current = null;
    setAtState(null);
    setState({ ...INITIAL_STATE, statusText: "Switching to active campaign…" });
    setRay(EMPTY_RAY);
    setRevalCount((c) => c + 1);
  }

  // The same file mtime answers "the head" and "offset N" differently, so a head validator
  // replayed here would 304 the fold away before it was ever fetched.
  const setAt = useCallback((offset: number | null) => {
    atRef.current = offset;
    lastModifiedRef.current = null;
    setAtState(offset);
    setRevalCount((c) => c + 1);
  }, []);

  useEffect(() => {
    if (!path) {
      setState({
        ...INITIAL_STATE,
        statusText: "No active campaign",
        statusHint:
          "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.",
      });
    }
  }, [path]);

  const tickDash = async (signal: AbortSignal) => {
    const id = cycleRef.current;
    const cmp = campaignRef.current;
    const p = pathRef.current;
    if (!id || !cmp || !p) return;
    try {
      const resp = await fetchDashboardByPath(p, lastModifiedRef.current, signal, atRef.current);
      if (signal.aborted) return;
      goneRef.current = 0;

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

      if (resp.validator) lastModifiedRef.current = resp.validator;

      if (isWarming(resp.data)) {
        stampMismatchRef.current = 0;
        const stillComing = hasLiveProducer((resp.data as WarmingSnapshot).run_phase);
        setState((prev) => ({
          ...prev,
          dash: null,
          status: "stale",
          statusText: stillComing ? "Origin running" : "No snapshot was ever written",
          statusHint: stillComing
            ? "First snapshot lands when origin completes — campaign is initialising."
            : "The run stopped before its first snapshot. Nothing to show for this cycle.",
          termKey: "status_warming_up",
          error: null,
          isLive: false,
          phase: "warming_up",
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
        stampMismatchRef.current += 1;
        if (stampMismatchRef.current >= STAMP_MISMATCH_LIMIT) {
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
      stampMismatchRef.current = 0;
      if (dash.run_phase !== lastPhaseRef.current) {
        const first = lastPhaseRef.current === null;
        lastPhaseRef.current = dash.run_phase;
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
        error: null,
        isLive: bucket.status === "live" && dash.run_phase === "running",
        phase: typeof dash.state === "string" ? dash.state : null,
      }));
    } catch (e) {
      if ((e as Error).name === "AbortError" || signal.aborted) return;
      onAuthError(e);
      reportIncident(e, { surface: "dashboard", address: unitKeyRef.current });

      // The route answers `warming_up` at 200 for a cycle with no dashboard yet, so a 404 here
      // means the cycle dir itself is gone.
      if (failureKind(e) === "gone") {
        goneRef.current += 1;
        if (goneRef.current >= GONE_CONFIRM_LIMIT && unitKeyRef.current) {
          reportAddressGone(unitKeyRef.current);
        }
        setState((prev) => ({
          ...prev,
          // Every number in the kept snapshot would describe a run no longer on disk.
          dash: null,
          status: "gone",
          statusText: "This campaign no longer exists",
          statusHint:
            "It was deleted, or its store was reset. Returning to the active run.",
          termKey: "status_gone",
          error: null,
          isLive: false,
        }));
        return;
      }
      goneRef.current = 0;
      setState((prev) => ({
        ...prev,
        status: "offline",
        statusText: "PromptPotter API unreachable",
        statusHint: "Reconnecting every 5 s — check the server is running.",
        termKey: "status_offline",
        error: (e as Error).message,
        // `dash.run_phase` stays untouched, so a client blip cannot read an in-flight cycle as gone.
        isLive: false,
      }));
    }
  };

  const tickRay = async (signal: AbortSignal) => {
    const p = pathRef.current;
    const key = unitKeyRef.current;
    if (!p) return;
    const known = rayEtagRef.current.key === key ? rayEtagRef.current.etag : null;
    try {
      const res = await fetchTimeRay(p, { limit: RAY_WINDOW }, known, signal);
      if (signal.aborted || unitKeyRef.current !== key) return;
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
    const p = pathRef.current;
    const key = unitKeyRef.current;
    if (!p || !cursor || loadingOlderRef.current) return;
    loadingOlderRef.current = true;
    void fetchTimeRay(p, { limit: RAY_WINDOW, before: cursor })
      .then((res) => {
        if (res.kind !== "ok" || unitKeyRef.current !== key) return;
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
