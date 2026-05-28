"use client";
// Single live-stream store. One provider polls dashboard.json; every consumer
// subscribes via `useCycleStream()`. The dashboard's `rounds[]` summary block
// is the sole "completed rounds" surface — round_NNNN.json is deep-audit
// only and fetched lazily via `useRoundFile`.

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { fetchDashboardConditional } from "./api";
import { ageTextSeconds } from "./format";
import type {
  OriginSummary,
  RoundSummary,
} from "./api/types";
import { rootCycleId } from "./ids";
import { usePoll } from "./usePoll";

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
  // Origin row — accuracy + sample count behind C0. Replaces the prior
  // pair of top-level `origin_accuracy` / `origin_samples` scalars.
  origin?: OriginSummary;
  // Completed-round display summaries; sole source of truth for the
  // FitnessChart, TrendChart, TopStrip sparkline, and LineageTree.
  // Sorted ascending by `round`; empty until the first round closes.
  rounds?: RoundSummary[];
  evaluators?: unknown[];
  scoring?: unknown;
  // Set on ``sample_started``, cleared on ``sample_scored``. The dataset
  // table pulses the row whose ``sample_id`` matches; ``null`` between
  // samples (no row pulses).
  current_sample_id?: number | null;
  // The adaptive queue mechanism's expected sample order under the
  // active objective — descending expected information gain
  // (``model``) or decision-verdict mutual information (``decision``),
  // refreshed per candidate at candidate-start. The dataset table sorts
  // by this when the operator's "sync with live sort" tick is on.
  // ``null`` before the first sorter fit lands.
  hard_sample_order?: number[] | null;
  // Fresh-campaign placeholder — set by the server when `dashboard.json`
  // hasn't been written yet (origin still running). The companion
  // `phase_hint` names what's blocking; the rest of the shape is empty.
  warming_up?: boolean;
  phase_hint?: string;
  // Structured crash summary projected from the canonical ``ErrorRecord``
  // ledger entry. Sole writer: ``LiveDashboardView._handle_error``. Absent
  // on normal stops; ``RunErrorBanner`` surfaces this above the TopStrip.
  error?: {
    kind: string;
    message: string;
    stop_reason: string;
  };
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

// Shape-agnostic round-file document. Deep audit consumers (FreqChart,
// ScoringInspector, OptimizerNodeDetail) fetch one of these lazily via
// `useRoundFile`; the stream itself no longer maintains a rolling array.
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

// Internal hook backing the provider. Polls dashboard.json every
// `intervalMs` and resets on cycleId change via the prev-prop pattern so the
// prior cycle's snapshot can't linger during the new fetch. `campaignId` is
// needed because a `cycle_id` is unique only within its campaign — every
// per-cycle fetch carries both.
function useCycleStreamSource(
  campaignId: string | null,
  cycleId: string | null,
  intervalMs: number,
): CycleStreamState {
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  // Bumped on every unit switch so `usePoll` fires an immediate tick — the
  // hand-rolled loop used to restart (and tick at once) on each cycle change.
  const [revalCount, setRevalCount] = useState(0);
  const cycleRef = useRef<string | null>(null);
  const campaignRef = useRef<string | null>(null);
  // Consecutive dashboard.json payloads whose identity stamp didn't match
  // the polled unit. Once it crosses STAMP_MISMATCH_LIMIT the banner says
  // so — a never-matching stamp can't leave the UI silently on "Connecting…".
  const stampMismatchRef = useRef(0);
  // Last server-issued `Last-Modified` for this unit's dashboard.json.
  // Sent back as `If-Modified-Since` next tick so the server can short-
  // circuit with 304 when the file mtime hasn't advanced. Reset on unit
  // switch so a stale value from the prior unit can't suppress the
  // first real fetch of the new unit.
  const lastModifiedRef = useRef<string | null>(null);

  // Change-detect on the COMPOSITE unit identity (campaign + cycle). A
  // cycle_id is unique only within its campaign — switching to another
  // campaign whose root unit shares this cycle_id must still reset the
  // stream, or the prior campaign's dashboard lingers forever.
  const unitKeyRef = useRef<string | null>(null);
  const unitKey =
    campaignId && cycleId ? `${campaignId} ${cycleId}` : null;
  if (unitKeyRef.current !== unitKey) {
    unitKeyRef.current = unitKey;
    cycleRef.current = cycleId;
    campaignRef.current = campaignId;
    stampMismatchRef.current = 0;
    lastModifiedRef.current = null;
    // Identity changed — hard-reset every cycle-scoped field so the prior
    // unit's dash snapshot and `● Live` badge can't linger for a frame
    // while the first poll of the new unit is in flight.
    setState({ ...INITIAL_STATE, statusText: "Switching to active campaign…" });
    setRevalCount((c) => c + 1);
  }

  // No active campaign — the static prompt. The poll itself is gated off
  // via `enabled` on the usePoll call below.
  useEffect(() => {
    if (!cycleId || !campaignId) {
      setState({
        ...INITIAL_STATE,
        statusText: "No active campaign",
        statusHint:
          "Start a campaign: `python -m promptpotter new <dataset>` in another terminal.",
      });
    }
  }, [campaignId, cycleId]);

  // The poll tick. `usePoll` owns the interval, the hidden-tab pause, and
  // the per-tick AbortController; this fetches dashboard.json (via
  // If-Modified-Since so unchanged ticks 304 cheaply) and guards its
  // identity stamp. The completed-round summary block rides this same
  // payload (`dash.rounds[]`) — no second fetch path.
  const tick = async (signal: AbortSignal) => {
    const id = cycleRef.current;
    const cmp = campaignRef.current;
    if (!id || !cmp) return;
    try {
      // dashboard.json is per-session — it lives in the session's root
      // cycle dir, shared by that session's forks. The server resolves
      // the session-family root from the viewed cycle id.
      const resp = await fetchDashboardConditional(
        cmp,
        id,
        lastModifiedRef.current,
        signal,
      );
      if (signal.aborted) return;
      if (resp.lastModified) lastModifiedRef.current = resp.lastModified;

      // 304 — file mtime hasn't advanced since the last fetch. Skip the
      // setState entirely unless the age bucket crossed a threshold
      // (Live → Stale → Snapshot); a no-op `setState(prev => prev)` is
      // bailed-out by React, so consumers don't re-render.
      if (resp.kind === "not_modified") {
        setState((prev) => {
          const wall = Date.parse(prev.dash?.wallclock_serialized_at || "");
          const ageS = Number.isFinite(wall) ? (Date.now() - wall) / 1000 : null;
          const bucket = ageBucket(ageS);
          if (prev.termKey === bucket.termKey) return prev;
          return {
            ...prev,
            status: bucket.status,
            statusText: bucket.statusText,
            statusHint: bucket.statusHint,
            ageS,
            termKey: bucket.termKey,
            isLive: bucket.status === "live",
          };
        });
        return;
      }

      const dash = resp.data as DashboardSnapshot;

      // Fresh-campaign warming_up payload (server returns this at 200 when
      // dashboard.json doesn't exist yet — typically while origin is
      // running). Distinct user-visible status; no charts to render until
      // the first real snapshot lands.
      if (dash.warming_up === true) {
        stampMismatchRef.current = 0;
        setState((prev) => ({
          ...prev,
          dash,
          status: "stale",
          statusText: "Origin running",
          statusHint:
            "First snapshot lands when origin completes — campaign is initialising.",
          termKey: "status_warming_up",
          ageS: null,
          error: null,
          isLive: false,
          phase: "warming_up",
        }));
        return;
      }

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
    } catch (e) {
      if ((e as Error).name === "AbortError" || signal.aborted) return;
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

  usePoll(tick, {
    intervalMs,
    enabled: !!campaignId && !!cycleId,
    revalidateOn: revalCount,
  });

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
