// Polling lifecycle — port of vanilla startPolling/stopPolling at
// webapp/index.html:1002. Pause on tab hide so a backgrounded UI doesn't keep
// firing fetches; resume on visible. AbortController cancels the previous
// fetch so stalled requests can't pile up.

import { useEffect, useRef, useState } from "react";
import { fetchCycleFile } from "./api";

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
  evaluators?: unknown[];
  scoring?: unknown;
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

export interface DashboardState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  ageS: number | null;
  termKey: string;
  error: string | null;
}

const INITIAL_STATE: DashboardState = {
  dash: null,
  status: "offline",
  statusText: "Connecting…",
  statusHint: "",
  ageS: null,
  termKey: "status_offline",
  error: null,
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

export function useDashboardPoll(cycleId: string | null, intervalMs = 2000): DashboardState {
  const [state, setState] = useState<DashboardState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cycleRef = useRef<string | null>(null);

  // Clear the snapshot when cycleId changes. The first tick of the new
  // cycle's polling cycle is async — without this, every percentage cell
  // keeps rendering the prior cycle's `best` / `origin_accuracy` until the
  // new dashboard.json fetch lands, surfacing values the active cycle has
  // not confirmed.
  if (cycleRef.current !== cycleId) {
    cycleRef.current = cycleId;
    if (state.dash !== null) {
      setState((prev) => ({ ...prev, dash: null }));
    }
  }

  useEffect(() => {
    if (!cycleId) {
      setState({
        dash: null,
        status: "offline",
        statusText: "No active session",
        statusHint:
          "Start a cycle: `python -m promptpotter optimize --backend-url http://127.0.0.1:8000 --config datasets/<name>/campaign.json` in another terminal.",
        ageS: null,
        termKey: "status_offline",
        error: null,
      });
      return;
    }

    let cancelled = false;

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
        setState({
          dash,
          status: bucket.status,
          statusText: bucket.statusText,
          statusHint: bucket.statusHint,
          ageS,
          termKey: bucket.termKey,
          error: null,
        });
      } catch (e) {
        if ((e as Error).name === "AbortError") return;
        if (cancelled) return;
        setState((prev) => ({
          ...prev,
          status: "offline",
          statusText: `dashboard.json unreachable for ${id}`,
          statusHint: "Start the optimizer: `python -m promptpotter optimize`",
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
