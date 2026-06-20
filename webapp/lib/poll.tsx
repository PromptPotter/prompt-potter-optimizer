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
import { liveCandidateId } from "@/lib/candidate-label";
import { fetchDashboardConditional } from "./api";
import { useAuthGate } from "./auth-context";
import { resolveRunPhase } from "./run-phase";
import { ageTextSeconds } from "./format";
import type { RoundSummary, SpendRollup } from "./api/types";
import type { SampleOrderStep } from "./types/round";
import { usePoll } from "./hooks/usePoll";

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
  // Fine-grained activity (origin / scoring / l1_generate / between_samples / …)
  // plus the terminal "stopped". Lifted to `phase` for transient gating.
  state?: string;
  // The single run-state value (RunPhase), declared by the runner and projected
  // by LiveDashboardView: running | paused | stopping | terminal. The webapp
  // reads this — it does NOT re-derive "running" from `state` freshness. The
  // server-side cycle list adds "detached"; the live view composes that as
  // run_phase==="running" + connection offline (see `isLive`).
  run_phase?: "running" | "paused" | "gate" | "stopping" | "terminal";
  // Terminal reason (raw StopReason value) once run_phase==="terminal".
  stop_reason?: string;
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
  // Completed-round display summaries; sole source of truth for the
  // FitnessChart, TrendChart, TopStrip sparkline, and LineageTree.
  // Sorted ascending by `round`; empty until the first round closes.
  rounds?: RoundSummary[];
  evaluators?: unknown[];
  scoring?: unknown;
  // Two-bucket spend rollup. Always present in a real dashboard.json (the Python
  // LiveDashboardState.spend has a default_factory); absent only on the
  // `warming_up` placeholder — hence optional here, firm once present.
  spend?: SpendRollup;
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
  // Declared run-limit ceilings, written at INIT.exit from the cycle's
  // OptimizationConfig. Static (unlike the live `patience` "N/max" string) —
  // the operator-facing source the fork reconcile dialog defaults against
  // ("3 of 6 rounds left"). A fork re-emits its own reconciled limits here.
  run_limits?: {
    max_rounds?: number | null;
    l1_patience?: number;
    l2_patience?: number | null;
    l3_patience?: number | null;
    pobb_epsilon?: number;
    spend_budget_usd?: number | null;
    token_budget?: number | null;
  };
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
  // Optimizer-loop degradations the self-healing rails recovered from
  // (zero-candidate round, L2 framing soft-reject, injection truncation),
  // projected from the canonical ``RoundWarningRecord``. Sole writer:
  // ``LiveDashboardView._handle_round_warning``. Rolling + capped; surfaced
  // by ``RunWarningsBanner`` below the crash banner.
  recent_loop_warnings?: {
    ts: string;
    kind: string;
    severity: "warning" | "error";
    message: string;
    round?: number | null;
    detail?: Record<string, unknown>;
  }[];
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
    hits?: number;
    total?: number;
  };
}

// `current_round.nodes.l1_score.input.candidates[]` shape — the *input* half
// of the live l1_score block (mirrors round_NNNN.json::candidate_scores for
// the seed-able fields). Carries the candidate's evolved searchpoint:
// `prompt_fields` (OptSearchPoint.prompt_field_dict() shape) + `pp_override`
// (its pipeline_params delta). Read by `liveCandidateSearchPoint` so the steer
// panel can seed from a still-in-flight candidate without the round file.
export interface LiveInputCandidate {
  idx?: number;
  label?: string;
  changes_description?: string;
  pp_override?: Record<string, unknown> | null;
  prompt_fields?: Record<string, unknown>;
  // Server-resolved, config-only effective params (`{node:{param:value}, steps}`),
  // prompt stripped. The in-flight peer of round_NNNN.json::candidate_scores[].
  // resolved_pipeline_params — read by `liveObserveConfig` for the OBSERVE view.
  resolved_pipeline_params?: Record<string, unknown> | null;
}

export interface L1ScoreOutput {
  candidates?: LiveCandidate[];
}

export interface L1ScoreInput {
  candidates?: LiveInputCandidate[];
}

// Shared frozen empty result for the no-candidate path so every consumer
// gets a stable reference. A fresh `[]` per call gave each poll a new array
// identity, churning the FitnessPanel Set chain
// (realApplicable→viewApplicable→inActive) into an unbounded setState loop
// once a real cycleId resolved post-login.
const NO_CANDIDATES: LiveCandidate[] = Object.freeze([] as LiveCandidate[]) as LiveCandidate[];

export function liveL1Candidates(dash: DashboardSnapshot | null): LiveCandidate[] {
  const nodes = dash?.current_round?.nodes;
  if (!nodes || typeof nodes !== "object") return NO_CANDIDATES;
  const l1 = (nodes as Record<string, { output?: L1ScoreOutput }>).l1_score;
  return l1?.output?.candidates ?? NO_CANDIDATES;
}

const NO_INPUT_CANDIDATES: LiveInputCandidate[] = Object.freeze(
  [] as LiveInputCandidate[],
) as LiveInputCandidate[];

// The seed-able input half of the live l1_score block — the in-flight peer of
// `round_NNNN.json::candidate_scores` for steer-fork seeding.
export function liveL1InputCandidates(
  dash: DashboardSnapshot | null,
): LiveInputCandidate[] {
  const nodes = dash?.current_round?.nodes;
  if (!nodes || typeof nodes !== "object") return NO_INPUT_CANDIDATES;
  const l1 = (nodes as Record<string, { input?: L1ScoreInput }>).l1_score;
  return l1?.input?.candidates ?? NO_INPUT_CANDIDATES;
}

// Match a selection's `candidate_id` back to a live candidate slot. Read peer of
// `liveCandidateId` (which mints the id): construction and match ride one rule —
// the same idx guard on both sides — so a selection can't resolve on the output
// half and miss on the input half. null when no in-flight candidate carries the
// id (a malformed idx is skipped, never stringified to `r{round}_undefined`).
function matchLiveCandidate<T extends { idx?: number }>(
  candidates: readonly T[],
  round: number | null,
  candidateId: string,
): T | null {
  for (const c of candidates) {
    const i = Number(c.idx);
    if (!Number.isFinite(i) || i < 0) continue;
    if (liveCandidateId(round, i) === candidateId) return c;
  }
  return null;
}

// Output-candidate slot (the scored half).
export const liveCandidate = (
  dash: DashboardSnapshot | null,
  round: number | null,
  candidateId: string,
): LiveCandidate | null =>
  matchLiveCandidate(liveL1Candidates(dash), round, candidateId);

// Input-candidate slot — the seed-able prompt_fields / pp_override half, for
// steer-fork seeding from a still-in-flight candidate.
export const liveInputCandidate = (
  dash: DashboardSnapshot | null,
  round: number | null,
  candidateId: string,
): LiveInputCandidate | null =>
  matchLiveCandidate(liveL1InputCandidates(dash), round, candidateId);

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
  sample_order_timeline?: SampleOrderStep[];
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
  // The optimizer is actively executing this cycle — the composition of the two
  // orthogonal axes: the run declares `run_phase === "running"` AND the
  // connection is fresh (`status === "live"`). A paused run declares "paused"
  // → isLive false immediately (no 30 s freshness lag — the old bug). A
  // silently-dead producer ("detached") keeps run_phase "running" on disk but
  // its poll goes stale → isLive false. The single gate for every transient
  // indicator (blinking rows, pulsing nodes, the round-strip "live" pill, the
  // Stop affordance). Computed once here; consumers never re-derive it.
  isLive: boolean;
  // The connection-aware RunPhase for display (running/paused/stopping/detached/
  // terminal). Same value the cycle list's derive_run_phase emits: the runner's
  // declared `run_phase`, but a `running` cycle whose poll has gone quiet reads
  // `detached`. Surfaces show this, not raw `dash.run_phase`, so a silently-dead
  // producer is labelled `detached` here exactly as it is in the picker.
  runPhaseResolved: string | null;
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
  runPhaseResolved: null,
  phase: null,
};

// Consecutive stamp-mismatch drops before the banner surfaces the problem.
// 3 drops ≈ 6s at the 2s cadence — long enough to ride out a one-tick
// re-instantiation during `new`, short enough the operator isn't left
// staring at "Connecting…" with no reason.
const STAMP_MISMATCH_LIMIT = 3;

// Reconnect cadence while the API is unreachable — slower than the live poll so
// a downed server is retried efficiently (every 5 s) rather than hammered. See
// the two-cadence note at the `usePoll` call.
const RECONNECT_INTERVAL_MS = 5000;

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

// Seconds since the dashboard self-stamped `wallclock_serialized_at`. Sole
// reader of that field's age — feeds `ageBucket`. Unparseable/missing → null.
function wallclockAgeS(iso: string | undefined): number | null {
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
  // Poll only with a confirmed session; a 401 mid-run re-probes /auth/me so
  // the loop halts instead of storming the server (see useAuthGate).
  const { authed, onAuthError } = useAuthGate();
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
          const ageS = wallclockAgeS(prev.dash?.wallclock_serialized_at);
          const bucket = ageBucket(ageS);
          if (prev.termKey === bucket.termKey) return prev;
          return {
            ...prev,
            status: bucket.status,
            statusText: bucket.statusText,
            statusHint: bucket.statusHint,
            ageS,
            termKey: bucket.termKey,
            isLive: bucket.status === "live" && prev.dash?.run_phase === "running",
            runPhaseResolved: resolveRunPhase(prev.dash?.run_phase, bucket.status === "live"),
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
          runPhaseResolved: null,
          phase: "warming_up",
        }));
        return;
      }

      // Payload-identity guard: dashboard.json self-stamps the cycle it
      // describes (per-cycle — every cycle owns its own file, stamped with
      // its own cycle_id). Drop any payload that doesn't match the unit we
      // polled for — a late response from the prior cycle, or a transient
      // identity/payload disagreement during a `new` or a cycle switch.
      // Stale data never reaches the UI; the next tick retries against the
      // correct unit.
      if (dash.campaign_id !== cmp || dash.cycle_id !== id) {
        const reported = `(${dash.campaign_id ?? "none"}, ${dash.cycle_id ?? "none"})`;
        const expected = `(${cmp}, ${id})`;
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
      const ageS = wallclockAgeS(dash.wallclock_serialized_at);
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
        isLive: bucket.status === "live" && dash.run_phase === "running",
        runPhaseResolved: resolveRunPhase(dash.run_phase, bucket.status === "live"),
        phase: typeof dash.state === "string" ? dash.state : null,
      }));
    } catch (e) {
      if ((e as Error).name === "AbortError" || signal.aborted) return;
      // A 401 means the session died — re-probe /auth/me so the gate flips
      // unauthed and this loop stops instead of 401-storming the server.
      onAuthError(e);
      setState((prev) => ({
        ...prev,
        status: "offline",
        statusText: "PromptPotter API unreachable",
        statusHint: "Reconnecting every 5 s — check the server is running.",
        termKey: "status_offline",
        error: (e as Error).message,
        isLive: false,
        // Unreachable producer: a cycle that was running now reads detached.
        runPhaseResolved: resolveRunPhase(prev.dash?.run_phase, false),
      }));
    }
  };

  // Two cadences. Reachable (live/stale) → the responsive `intervalMs` (2 s,
  // mostly 304-cheap; user actions fire an immediate tick via `revalidateOn`).
  // Offline → a steady 5 s reconnect probe, so a downed API recovers within
  // ~5 s without hammering. `usePoll` restarts its timer when this changes.
  const effectiveInterval = state.status === "offline" ? RECONNECT_INTERVAL_MS : intervalMs;
  usePoll(tick, {
    intervalMs: effectiveInterval,
    enabled: !!campaignId && !!cycleId && authed,
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
