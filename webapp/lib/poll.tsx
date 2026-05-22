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
import { fetchCycleFile, fetchDashboard, fetchFiles } from "./api";
import { rootCycleId } from "./ids";

export type StatusKind = "live" | "stale" | "offline";

export interface DashboardSnapshot {
  // Loose typing — dashboard.json is operator-facing JSON; we tolerate shape drift
  // and drill down at the use site.
  // Self-identity stamp written by LiveDashboardView — the session-family
  // this file describes. The poll guard (`tick`) drops any payload whose
  // stamp doesn't match the unit it asked for, so a stale response can
  // never render under the wrong header.
  campaign_id?: string;
  cycle_id?: string;
  session_id?: string;
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
  // ``picker_objective`` — descending expected information gain
  // (``model``) or decision-verdict mutual information (``decision``),
  // refreshed per candidate at candidate-start. The dataset table sorts
  // by this when the operator's "sync with live sort" tick is on.
  // ``null`` before the first sorter fit lands.
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
  // `status === "live"` — the optimizer wrote dashboard.json within the
  // freshness window. The single gate for every transient indicator (blinking
  // rows, pulsing nodes, "rolling" badges): when the producer dies the
  // freshness signal goes stale and `isLive` flips false, so all motion stops
  // without any per-component timeout logic.
  isLive: boolean;
  // `dash.state` lifted to the top level so transient indicators can gate on
  // the phase (e.g. only blink a sample row when `phase === "scoring"`).
  phase: string | null;
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
  isLive: false,
  phase: null,
};

// Consecutive stamp-mismatch drops before the banner surfaces the problem.
// 3 drops ≈ 6s at the 2s cadence — long enough to ride out a one-tick
// re-instantiation during `new`, short enough the operator isn't left
// staring at "Connecting…" with no reason.
const STAMP_MISMATCH_LIMIT = 3;

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

// Internal hook backing the provider. Polls dashboard.json every
// `intervalMs`; on each round change refreshes the round-files cache. Both
// reset on cycleId change via the prev-prop pattern so the prior cycle's
// snapshots can't linger during the new fetch. `campaignId` is needed
// because a `cycle_id` is unique only within its campaign — every
// per-cycle fetch carries both.
function useCycleStreamSource(
  campaignId: string | null,
  cycleId: string | null,
  intervalMs: number,
): CycleStreamState {
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const cycleRef = useRef<string | null>(null);
  const campaignRef = useRef<string | null>(null);
  // Highest round number seen on the live dashboard; bumping it triggers a
  // single rounds-listing refresh. Distinct from the most recent round file
  // we hold — that's encoded in `state.rounds`.
  const lastRoundRef = useRef<number | null>(null);
  const roundsFetchingRef = useRef(false);
  // Consecutive dashboard.json payloads whose identity stamp didn't match
  // the polled unit. Once it crosses STAMP_MISMATCH_LIMIT the banner says
  // so — a never-matching stamp can't leave the UI silently on "Connecting…".
  const stampMismatchRef = useRef(0);

  // Change-detect on the COMPOSITE unit identity (campaign + cycle). A
  // cycle_id is unique only within its campaign — switching to another
  // campaign whose root unit shares this cycle_id must still reset the
  // stream, or the prior campaign's dashboard lingers forever.
  const unitKeyRef = useRef<string | null>(null);
  const unitKey =
    campaignId && cycleId ? `${campaignId} ${cycleId}` : null;
  if (unitKeyRef.current !== unitKey) {
    unitKeyRef.current = unitKey;
    cycleRef.current = cycleId;
    campaignRef.current = campaignId;
    lastRoundRef.current = null;
    stampMismatchRef.current = 0;
    // Identity changed — hard-reset every cycle-scoped field so the prior
    // unit's rounds, dash snapshot, and `● Live` badge can't linger for a
    // frame while the first poll of the new unit is in flight.
    setState({ ...INITIAL_STATE, statusText: "Switching to active campaign…" });
  }

  useEffect(() => {
    if (!cycleId || !campaignId) {
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
      const cmp = campaignRef.current;
      if (!id || !cmp || id !== cycleId || roundsFetchingRef.current) return;
      roundsFetchingRef.current = true;
      try {
        const listing = await fetchFiles(cmp, id);
        if (cancelled || cycleRef.current !== id) return;
        const roundFiles = listing.entries
          .filter(
            (e) => e.scope === "cycle" && /^rounds\/round_\d+\.json$/.test(e.path),
          )
          .sort((a, b) => a.path.localeCompare(b.path));
        const fetched = await Promise.all(
          roundFiles.map(async (f) => {
            try {
              const r = await fetchCycleFile(cmp, id, "cycle", f.path);
              return r.content ? (JSON.parse(r.content) as RoundFileDoc) : null;
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
      const cmp = campaignRef.current;
      if (!id || !cmp) return;
      try {
        // dashboard.json is per-session — it lives in the session's root
        // cycle dir, shared by that session's forks. The server resolves
        // the session-family root from the viewed cycle id.
        const dash = (await fetchDashboard(cmp, id, ctrl.signal)) as DashboardSnapshot;
        if (cancelled) return;
        // Payload-identity guard: dashboard.json self-stamps the
        // session-family it describes. Drop any payload that doesn't match
        // the unit we polled for — a late response from the prior cycle, or
        // a transient identity/payload disagreement during a `new` or a
        // cycle switch. Stale data never reaches the UI; the next tick
        // retries against the correct unit.
        if (dash.campaign_id !== cmp || dash.cycle_id !== rootCycleId(id)) {
          const reported = `(${dash.campaign_id ?? "none"}, ${dash.cycle_id ?? "none"})`;
          const expected = `(${cmp}, ${rootCycleId(id)})`;
          console.debug(
            `[cycle-stream] dropped dashboard payload — stamp ${reported} != unit ${expected}`,
          );
          stampMismatchRef.current += 1;
          // One re-instantiation tick is normal; a stamp that *never* matches
          // is a real fault — surface it instead of polling silently forever.
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
        // A matching payload clears any prior mismatch streak.
        stampMismatchRef.current = 0;
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
          isLive: bucket.status === "live",
          phase: typeof dash.state === "string" ? dash.state : null,
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
          isLive: false,
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
  }, [campaignId, cycleId, intervalMs]);

  return state;
}

export function CycleStreamProvider({
  campaignId,
  cycleId,
  intervalMs = 2000,
  children,
}: {
  campaignId: string | null;
  cycleId: string | null;
  intervalMs?: number;
  children: ReactNode;
}) {
  const state = useCycleStreamSource(campaignId, cycleId, intervalMs);
  return (
    <CycleStreamContext.Provider value={state}>{children}</CycleStreamContext.Provider>
  );
}
