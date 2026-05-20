"use client";
// Single live-stream store. One provider polls dashboard.json + maintains a
// rolling array of round_NNNN.json docs; every consumer subscribes via
// `useCycleStream()`. Replaces the prior split of `useDashboardPoll` +
// seven independent `useRoundHistory(cycleId, refreshKey)` calls — those
// drifted by a render whenever they ran their own listing + Promise.all on
// different effect cycles.

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchCycleFile, fetchFiles } from "./api";

export type StatusKind = "live" | "stale" | "offline";

export interface DashboardSnapshot {
  // Loose typing — dashboard.json is operator-facing JSON; we tolerate shape drift
  // and drill down at the use site.
  state?: string;
  round?: number;
  query?: string;
  best?: number;
  total_queries_scored?: number;
  last_query_elapsed_s?: number;
  wallclock_serialized_at?: string;
  current_round?: {
    round?: number;
    nodes?: Record<string, unknown>;
    pobb?: {
      current_id?: string;
      n_samples?: number;
      leader_prob?: number;
      posterior_width?: number;
      top?: { id: string; p_best: number }[];
    };
  };
  origin_accuracy?: number;
  // Sample count behind the origin score — drives the C0 bar's over-bar
  // label live, before round 1's file lands on disk.
  origin_samples?: number;
  evaluators?: unknown[];
  scoring?: unknown;
  // Set on ``sample_started``, cleared on ``sample_scored``. The dataset
  // table pulses the row whose ``sample_id`` matches; ``null`` between
  // samples (no row pulses).
  current_sample_id?: number | null;
  // The adaptive picker's expected sample order under the active
  // ``picker_objective`` (Fisher info / Chernoff info), refreshed per
  // candidate at candidate-start. The dataset table sorts by this when
  // the operator's "sync with live sort" tick is on. ``null`` before
  // the first sorter fit lands.
  hard_sample_order?: number[] | null;
  [key: string]: unknown;
}

// `current_round.nodes.l1_score.output.candidates[]` shape — the live in-flight
// projection of the round in progress. Samples are compact strings during the
// round (per `fmt_sample_line` in live_dashboard.py) and dicts once the round
// completes. Every consumer drills into this same path; `liveL1Candidates`
// narrows once so the call sites don't repeat the `as Record<...>` cast.
export interface LiveSample {
  sample_id?: number;
  hit?: boolean;
  prediction?: string;
  cached?: boolean;
  time_s?: number;
  terminated_at?: string;
  input_tokens?: number;
  output_tokens?: number;
}

export interface LiveCandidate {
  idx?: number;
  label?: string;
  model?: string;
  samples?: (LiveSample | string)[];
  stats?: {
    accuracy?: number;
    composite_fitness?: number;
    composite_fitness_formula?: string | null;
    evaluators?: Record<string, number>;
  };
}

export interface L1ScoreOutput {
  candidates?: LiveCandidate[];
}

export function liveL1Candidates(dash: DashboardSnapshot | null): LiveCandidate[] {
  const nodes = dash?.current_round?.nodes;
  if (!nodes || typeof nodes !== "object") return [];
  const l1 = (nodes as Record<string, { output?: L1ScoreOutput }>).l1_score;
  return l1?.output?.candidates ?? [];
}

// Shape-agnostic round-file document. Every consumer casts to its own
// narrowed view at the use site; the provider is the single fetch path so the
// dashboard makes one set of round-file network calls per round change
// instead of one per consuming component.
export interface RoundFileDoc {
  round?: number;
  accuracy?: number;
  composite_fitness?: number;
  origin_accuracy?: number;
  scoreboard?: unknown[];
  results?: unknown[];
  candidate_scores?: unknown[];
  all_candidate_results?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface CycleStreamState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  ageS: number | null;
  termKey: string;
  error: string | null;
  // Sorted ascending by `round`. Empty until the first listing fetch lands.
  rounds: RoundFileDoc[];
}

const INITIAL_STATE: CycleStreamState = {
  dash: null,
  status: "offline",
  statusText: "Connecting…",
  statusHint: "",
  ageS: null,
  termKey: "status_offline",
  error: null,
  rounds: [],
};

// Status banner age buckets — same thresholds as vanilla setStatus call sites.
export interface BucketResult {
  status: StatusKind;
  statusText: string;
  statusHint: string;
  termKey: string;
}

// Canonical round number across the dashboard. `current_round.round` is the
// authoritative field (live_dashboard.py:750); the top-level `round` is the
// inherited-from-prior-dashboard value used during cycle re-instantiation
// before any phase fires. Prefer the nested one, fall through to top-level,
// return null only when neither is a number.
export function roundOf(dash: DashboardSnapshot | null): number | null {
  const r = dash?.current_round?.round ?? dash?.round;
  return typeof r === "number" ? r : null;
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
  if (ageS < 30) {
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
    statusText: `Snapshot · last write ${(ageS / 60).toFixed(0)}m ago`,
    statusHint: "No live optimizer — viewing a frozen cycle",
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

// Internal hook backing the provider. Polls dashboard.json every
// `intervalMs`; on each round change refreshes the round-files cache. Both
// reset on cycleId change via the prev-prop pattern so the prior cycle's
// snapshots can't linger during the new fetch.
function useCycleStreamSource(
  cycleId: string | null,
  intervalMs: number,
): CycleStreamState {
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cycleRef = useRef<string | null>(null);
  // Highest round number seen on the live dashboard; bumping it triggers a
  // single rounds-listing refresh. Distinct from the most recent round file
  // we hold — that's encoded in `state.rounds`.
  const lastRoundRef = useRef<number | null>(null);
  const roundsFetchingRef = useRef(false);

  if (cycleRef.current !== cycleId) {
    cycleRef.current = cycleId;
    lastRoundRef.current = null;
    if (state.dash !== null || state.rounds.length > 0) {
      setState((prev) => ({ ...prev, dash: null, rounds: [] }));
    }
  }

  useEffect(() => {
    if (!cycleId) {
      setState({
        ...INITIAL_STATE,
        statusText: "No active campaign",
        statusHint:
          "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.",
      });
      return;
    }

    let cancelled = false;

    // Round-files refresh. Single-flight: if a prior fetch is still running,
    // the next round-change tick will pick it up. The listing is the SoT for
    // which round files exist on disk; we re-fetch the whole set rather than
    // diffing — round files are small JSON blobs and the prior arch proved
    // race-free with this shape.
    const refreshRounds = async () => {
      const id = cycleRef.current;
      if (!id || id !== cycleId || roundsFetchingRef.current) return;
      roundsFetchingRef.current = true;
      try {
        const listing = await fetchFiles(id);
        if (cancelled || cycleRef.current !== id) return;
        const roundFiles = listing.entries
          .filter(
            (e) => e.scope === "cycle" && /^rounds\/round_\d+\.json$/.test(e.path),
          )
          .sort((a, b) => a.path.localeCompare(b.path));
        const fetched = await Promise.all(
          roundFiles.map(async (f) => {
            try {
              const r = await fetchCycleFile(id, "cycle", f.path);
              return JSON.parse(r.content) as RoundFileDoc;
            } catch {
              return null;
            }
          }),
        );
        if (cancelled || cycleRef.current !== id) return;
        const valid = fetched
          .filter((p): p is RoundFileDoc => p !== null)
          .sort((a, b) => (a.round ?? 0) - (b.round ?? 0));
        setState((prev) => ({ ...prev, rounds: valid }));
      } catch {
        // listing failed — retry on the next round-change tick
      } finally {
        roundsFetchingRef.current = false;
      }
    };

    const tick = async () => {
      if (abortRef.current) abortRef.current.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      const id = cycleRef.current;
      if (!id) return;
      try {
        const r = await fetchCycleFile(id, "cycle", "dashboard.json", ctrl.signal);
        const dash = JSON.parse(r.content) as DashboardSnapshot;
        if (cancelled) return;
        const wall = Date.parse(dash.wallclock_serialized_at || "");
        const ageS = Number.isFinite(wall) ? (Date.now() - wall) / 1000 : null;
        const bucket = ageBucket(ageS);
        const liveRound = roundOf(dash);
        setState((prev) => ({
          ...prev,
          dash,
          status: bucket.status,
          statusText: bucket.statusText,
          statusHint: bucket.statusHint,
          ageS,
          termKey: bucket.termKey,
          error: null,
        }));
        if (liveRound != null && liveRound !== lastRoundRef.current) {
          lastRoundRef.current = liveRound;
          void refreshRounds();
        }
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          status: "offline",
          statusText: `dashboard.json unreachable for ${id}`,
          statusHint: "Resume the active campaign: `python -m promptpotter resume`",
          termKey: "status_offline",
          error: (e as Error).message,
        }));
      }
    };

    const start = () => {
      if (timerRef.current != null) return;
      timerRef.current = setInterval(tick, intervalMs);
      tick();
    };

    const stop = () => {
      if (timerRef.current != null) {
        clearInterval(timerRef.current);
        timerRef.current = null;
      }
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };

    const onVis = () => {
      if (document.hidden) stop();
      else start();
    };

    start();
    document.addEventListener("visibilitychange", onVis);

    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVis);
      stop();
    };
  }, [cycleId, intervalMs]);

  return state;
}

export function CycleStreamProvider({
  cycleId,
  intervalMs = 2000,
  children,
}: {
  cycleId: string | null;
  intervalMs?: number;
  children: ReactNode;
}) {
  const state = useCycleStreamSource(cycleId, intervalMs);
  return (
    <CycleStreamContext.Provider value={state}>{children}</CycleStreamContext.Provider>
  );
}
