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
import { reportIncident } from "@/lib/diagnostics";
import { failureKind, fetchDashboardByPath } from "./api";
import { encodeCyclePath, pathLeaf, type CyclePath } from "./ids";
import { useAuthGate } from "./auth-context";
import { ageTextSeconds } from "./format";
import type { DashboardCandidate, LiveDashboardState } from "./api/types";
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation } from "./revalidate";
import { isInFlight } from "./run-phase";
import { useWorkspace } from "./workspace";

// `gone` is NOT a flavour of `offline`, and conflating them is the bug this
// vocabulary exists to prevent: "the server didn't answer" and "the server
// answered, and says this no longer exists" call for opposite reactions — retry
// vs stop — and reporting the second as the first sends an operator to restart a
// server that was never down (frontend-surface-contract.md § I7).
export type StatusKind = "live" | "stale" | "offline" | "gone";

// `dashboard.json` IS `LiveDashboardState` — generated from the Pydantic model, not
// re-declared here. The hand-written version made every field optional and ended with
// `[key: string]: unknown`, so it typechecked anything (its `run_phase` union was
// missing two of RunPhase's six members, and nothing caught it).
//
// The server's `warming_up` placeholder is a DIFFERENT, 4-key shape — not a sparse
// dashboard. It is `WarmingSnapshot`, narrowed once in `tick` and never handed to a
// consumer, so no component has to ask whether its snapshot is real.
export type DashboardSnapshot = LiveDashboardState;

export interface WarmingSnapshot {
  warming_up: true;
  campaign_id: string;
  cycle_id: string;
  phase_hint: string;
  // Derived server-side like every other phase (`derive_run_phase`), and it is what
  // separates "no snapshot YET" from "no snapshot EVER": a cycle whose producer died
  // during init reads `detached`/`terminal` here while still having no dashboard.
  run_phase: string;
}

function isWarming(d: unknown): d is WarmingSnapshot {
  return !!d && typeof d === "object" && (d as WarmingSnapshot).warming_up === true;
}

// `current_round.nodes.l1_score.output.candidates[]` shape — the live in-flight
// projection of the round in progress. Samples are compact strings during the
// round (per `fmt_sample_line` in live_dashboard.py) and dicts once the round
// completes. Every consumer drills into this same path; `liveL1Candidates`
// narrows once so the call sites don't repeat the `as Record<...>` cast.
export interface LiveSample {
  sample_id?: number;
  fitness?: number;
  prediction?: string;
  cached?: boolean;
  time_s?: number;
  // The producer's own "this row errored" fact (`round_buffer.py::append_sample`). Without
  // it a reader has only `fitness`, whose absence is indistinguishable from a graded 0.
  error?: unknown;
  terminal_node?: string;
  input_tokens?: number;
  output_tokens?: number;
}

export interface LiveCandidate {
  idx?: number;
  label?: string;
  model?: string;
  samples?: (LiveSample | string)[];
  // No numbers here: they moved to `current_round.candidates`, in the same shape a closed
  // round serves, so no surface merges two shapes field by field. The tape is what this half
  // still owns. (The block also carries the value-inlined formula and the self-healing state
  // for a folder-UI reader; no component reads either, so neither is declared.)
}

// `current_round.nodes.l1_score.input.candidates[]` shape — the *input* half
// of the live l1_score block (mirrors round_NNNN.json::candidate_scores for
// the seed-able fields). Carries the candidate's evolved searchpoint:
// `prompt_fields` (OptSearchPoint.prompt_field_dict() shape) + the resolved
// config below.
export interface LiveInputCandidate {
  idx?: number;
  label?: string;
  changes_description?: string;
  // Present on settled `round_NNNN.json::candidate_scores[]` rows (the OBSERVE /
  // STEER readers locate by it); absent on in-flight rows, which match by idx.
  candidate_id?: string;
  prompt_fields?: Record<string, unknown>;
  // Server-resolved, config-only effective params (`{node:{param:value}, steps}`),
  // prompt stripped. The in-flight peer of round_NNNN.json::candidate_scores[].
  // resolved_pipeline_params — read by `liveObserveConfig` (OBSERVE view) and
  // `liveCandidateSearchPoint` (steer-fork seed from a still-in-flight candidate).
  resolved_pipeline_params?: Record<string, unknown> | null;
}

export interface L1ScoreOutput {
  candidates?: LiveCandidate[];
}

interface L1ScoreInput {
  candidates?: LiveInputCandidate[];
}

// Shared frozen empty result for the no-candidate path so every consumer
// gets a stable reference. A fresh `[]` per call gave each poll a new array
// identity, churning the candidates card's Set chain
// (realApplicable→viewApplicable→inActive) into an unbounded setState loop
// once a real cycleId resolved post-login.
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

// The in-flight round's candidate rows — SAME shape a closed round serves
// (`rounds[].candidates`), so a reader takes a whole row from whichever half has it. Filling one
// in from the other per field is what drew a bar from the 2 s dashboard poll and its error
// whisker from the 5 s tree poll.
export function liveCandidates(dash: DashboardSnapshot | null): DashboardCandidate[] {
  return dash?.current_round.candidates ?? NO_ROWS;
}

const NO_INPUT_CANDIDATES: LiveInputCandidate[] = Object.freeze(
  [] as LiveInputCandidate[],
) as LiveInputCandidate[];

// The seed-able input half of the live l1_score block — the in-flight peer of
// `round_NNNN.json::candidate_scores` for steer-fork seeding.
export function liveL1InputCandidates(
  dash: DashboardSnapshot | null,
): LiveInputCandidate[] {
  const nodes = dash?.current_round.nodes;
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

// Output-candidate slot — the sample tape. For a candidate's numbers, `liveCandidateRow`.
export const liveCandidate = (
  dash: DashboardSnapshot | null,
  round: number | null,
  candidateId: string,
): LiveCandidate | null =>
  matchLiveCandidate(liveL1Candidates(dash), round, candidateId);

// The in-flight row's NUMBERS, by the id the selection carries. Positional, matching
// `matchLiveCandidate` above and the id `roundCandidates` stamps on an in-flight row — one
// rule for construction and match, or a finished candidate resolves in neither.
export function liveCandidateRow(
  dash: DashboardSnapshot | null,
  round: number | null,
  candidateId: string,
): DashboardCandidate | null {
  const rows = liveCandidates(dash);
  for (let i = 0; i < rows.length; i++) {
    if (liveCandidateId(round, i) === candidateId) return rows[i] ?? null;
  }
  return null;
}

// Input-candidate slot — the seed-able prompt_fields / resolved_pipeline_params
// half, for steer-fork seeding from a still-in-flight candidate.
export const liveInputCandidate = (
  dash: DashboardSnapshot | null,
  round: number | null,
  candidateId: string,
): LiveInputCandidate | null =>
  matchLiveCandidate(liveL1InputCandidates(dash), round, candidateId);

export interface CycleStreamState {
  dash: DashboardSnapshot | null;
  status: StatusKind;
  statusText: string;
  statusHint: string;
  termKey: string;
  error: string | null;
  // The optimizer is actively executing this cycle — the composition of the two
  // orthogonal axes: the run declares `run_phase === "running"` AND the
  // connection is fresh (`status === "live"`). A paused run declares "paused"
  // → isLive false immediately (no 30 s freshness lag — the old bug). A
  // silently-dead producer ("detached") keeps run_phase "running" on disk but
  // its poll goes stale → isLive false. The single gate for every transient
  // indicator (blinking rows, pulsing nodes, the round-strip "live" pill, the
  // Pause affordance). Computed once here; consumers never re-derive it.
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

// Consecutive 404s before this poll declares its address dead. Same reasoning and
// same number as the stamp guard: one miss is a mint race (a cycle dir appearing
// under a pointer that already names it), three across ~6 s is a fact. The floor
// matters in the destructive direction — this verdict unpins the operator's view.
const GONE_CONFIRM_LIMIT = 3;

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

// Canonical round number across the dashboard. `current_round.round` is authoritative and is
// stamped from the projection's own `state.round` on every write, so it cannot lag the
// top-level one. The fall-through covers cycle re-instantiation, before any phase has fired.
export function roundOf(dash: DashboardSnapshot | null): number | null {
  const r = dash?.current_round.round ?? dash?.round;
  return typeof r === "number" ? r : null;
}

// Seconds since the dashboard self-stamped `wallclock_serialized_at`. Sole
// reader of that field's age — feeds `ageBucket`. Unparseable/missing → null.
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

// Internal hook backing the provider. Polls the viewed cycle's dashboard.json
// every `intervalMs` and resets on any change to the viewed PATH via the
// prev-prop pattern so the prior cycle's snapshot can't linger during the new
// fetch. The path is the single address: its root hop is the top-level cycle,
// deeper hops an L4 inner descendant. The stream re-roots to the LEAF hop's
// dashboard (the file it fetches + the identity stamp it must match).
function useCycleStreamSource(
  path: CyclePath | null,
  intervalMs: number,
): CycleStreamState {
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  // Poll only with a confirmed session; a 401 mid-run re-probes /auth/me so
  // the loop halts instead of storming the server (see useAuthGate).
  const { authed, onAuthError } = useAuthGate();
  // This poll is the authoritative existence read for the viewed address, so it
  // is the one that gets to declare it dead. `reportAddressGone` is identity-
  // stable by construction (workspace.tsx) — the tick must not re-arm on it.
  const { reportAddressGone } = useWorkspace();
  // Bumped on every unit switch so `usePoll` fires an immediate tick — the
  // hand-rolled loop used to restart (and tick at once) on each cycle change.
  const [revalCount, setRevalCount] = useState(0);
  const cycleRef = useRef<string | null>(null);
  const campaignRef = useRef<string | null>(null);
  // Consecutive dashboard.json payloads whose identity stamp didn't match
  // the polled unit. Once it crosses STAMP_MISMATCH_LIMIT the banner says
  // so — a never-matching stamp can't leave the UI silently on "Connecting…".
  const stampMismatchRef = useRef(0);
  // Consecutive 404s for the polled unit — see GONE_CONFIRM_LIMIT. Reset by the
  // unit-key guard below and by any answered tick, so only an UNBROKEN run of
  // misses counts.
  const goneRef = useRef(0);
  // Last server-issued `Last-Modified` for this unit's dashboard.json.
  // Sent back as `If-Modified-Since` next tick so the server can short-
  // circuit with 304 when the file mtime hasn't advanced. Reset on unit
  // switch so a stale value from the prior unit can't suppress the
  // first real fetch of the new unit.
  const lastModifiedRef = useRef<string | null>(null);
  // Last `run_phase` this poll observed for the unit. The same server-owned value
  // also rides `/cycles` (10 s) and `/tree` (5 s), so without a nudge the dock, the
  // sidebar and this stream sat up to 10 s apart on one transition — three surfaces
  // showing three states because they were observed at unrelated cadences, not
  // because they disagreed. Seeing it move here re-ticks the other two.
  const lastPhaseRef = useRef<string | null>(null);
  // The viewed path, held in a ref so the tick reads it without re-subscribing;
  // set in the same unit-key guard below.
  const pathRef = useRef<CyclePath | null>(null);

  // Change-detect on the whole viewed PATH. A cycle_id is unique only within
  // its campaign, and an inner descendant only within its parent's sandbox, so
  // the full encoded path IS the identity — any hop change (outer→outer,
  // outer→inner, inner→inner) hard-resets the stream, or a prior cycle's
  // dashboard lingers forever.
  const unitKeyRef = useRef<string | null>(null);
  const unitKey = path ? encodeCyclePath(path) : null;
  if (unitKeyRef.current !== unitKey) {
    unitKeyRef.current = unitKey;
    pathRef.current = path;
    // The EXPECTED stamp ids: the LEAF hop's own ids (its dashboard.json
    // self-stamps them — inner ids when descended, else the root).
    const leaf = path ? pathLeaf(path) : null;
    cycleRef.current = leaf?.cycleId ?? null;
    campaignRef.current = leaf?.campaignId ?? null;
    stampMismatchRef.current = 0;
    goneRef.current = 0;
    lastModifiedRef.current = null;
    lastPhaseRef.current = null;
    // Identity changed — hard-reset every cycle-scoped field so the prior
    // unit's dash snapshot and `● Live` badge can't linger for a frame
    // while the first poll of the new unit is in flight.
    setState({ ...INITIAL_STATE, statusText: "Switching to active campaign…" });
    setRevalCount((c) => c + 1);
  }

  // No active campaign — the static prompt. The poll itself is gated off
  // via `enabled` on the usePoll call below.
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

  // The poll tick. `usePoll` owns the interval, the hidden-tab pause, and
  // the per-tick AbortController; this fetches dashboard.json (via
  // If-Modified-Since so unchanged ticks 304 cheaply) and guards its
  // identity stamp. The completed-round summary block rides this same
  // payload (`dash.rounds[]`) — no second fetch path.
  const tick = async (signal: AbortSignal) => {
    const id = cycleRef.current;
    const cmp = campaignRef.current;
    const p = pathRef.current;
    if (!id || !cmp || !p) return;
    try {
      // One fetch for any depth: `fetchDashboardByPath` hits the root cycle's
      // dashboard route, riding `?descend=` for inner descendants. `cmp`/`id` are
      // the LEAF stamp ids the payload must self-report (inner ids when
      // descended), so the identity guard below is depth-agnostic.
      const resp = await fetchDashboardByPath(p, lastModifiedRef.current, signal);
      if (signal.aborted) return;
      // Any answer at all proves the address exists, so only an UNBROKEN run of
      // 404s can reach the confirm limit.
      goneRef.current = 0;

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
            termKey: bucket.termKey,
            isLive: bucket.status === "live" && prev.dash?.run_phase === "running",
          };
        });
        return;
      }

      // Only a 200 carries one. This route validates on mtime, so the validator IS a
      // Last-Modified date; the lineage-tree route validates on an ETag through the same
      // helper — hence the neutral field name.
      if (resp.validator) lastModifiedRef.current = resp.validator;

      // Fresh-campaign warming_up payload (server returns this at 200 when
      // dashboard.json doesn't exist yet — typically while origin is running). It is a
      // 4-key stub, NOT a sparse dashboard, so it is narrowed off here and never handed
      // downstream: `dash` stays null and consumers read the phase. No charts to render
      // until the first real snapshot lands.
      if (isWarming(resp.data)) {
        stampMismatchRef.current = 0;
        // "Initialising" is only true while something is still working on it. The
        // served phase is the one that knows: a producer that died before its first
        // flush leaves a cycle with no dashboard forever, and this branch used to
        // announce it as warming up for as long as the operator kept the tab open.
        const stillComing = isInFlight((resp.data as WarmingSnapshot).run_phase);
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

      // Payload-identity guard: dashboard.json self-stamps the cycle it
      // describes (per-cycle — every cycle owns its own file, stamped with
      // its own cycle_id). Drop any payload that doesn't match the unit we
      // polled for — a late response from the prior cycle, or a transient
      // identity/payload disagreement during a `new` or a cycle switch.
      // Stale data never reaches the UI; the next tick retries against the
      // correct unit.
      if (dash.campaign_id !== cmp || dash.cycle_id !== id) {
        const reported = `(${dash.campaign_id}, ${dash.cycle_id})`;
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
      if (dash.run_phase !== lastPhaseRef.current) {
        const first = lastPhaseRef.current === null;
        lastPhaseRef.current = dash.run_phase;
        // Not on the first observation: that is this unit's opening read, not a
        // transition, and bumping there would re-tick the workspace on every switch.
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
      // A 401 means the session died — re-probe /auth/me so the gate flips
      // unauthed and this loop stops instead of 401-storming the server.
      onAuthError(e);
      reportIncident(e, { surface: "dashboard", address: unitKeyRef.current });

      // THE dashboard read is this address's authoritative existence oracle: the
      // route answers `warming_up` at 200 while a cycle exists without a dashboard
      // yet, so a 404 here means the cycle dir itself is gone — deleted, reaped, or
      // reset away. That is terminal, and retrying it forever is what left the app
      // pinned to a dead campaign while announcing the SERVER was down.
      if (failureKind(e) === "gone") {
        goneRef.current += 1;
        if (goneRef.current >= GONE_CONFIRM_LIMIT && unitKeyRef.current) {
          reportAddressGone(unitKeyRef.current);
        }
        setState((prev) => ({
          ...prev,
          // Drop the snapshot with the address. It was fetched before the campaign
          // stopped existing, so every number in it now describes something that is
          // not on disk — rendering it would present a measurement for a deleted run.
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
        // Connection loss is presentation (see `status`), never a run phase —
        // `dash.run_phase` (and everything derived from it) is left untouched,
        // so a client blip can't make an in-flight cycle read as gone.
        isLive: false,
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
    // A confirmed `gone` STOPS the loop — there is nothing to reconnect to, and a
    // retry cadence would be a lie about the state. The unit-key guard resets
    // `status` on any address change, so the poll re-arms the moment the view
    // moves somewhere real (including the unpin this verdict just triggered).
    enabled: !!path && authed && state.status !== "gone",
    revalidateOn: revalCount,
  });

  return state;
}

export function CycleStreamProvider({
  path,
  intervalMs = 2000,
  children,
}: {
  // The single viewed-cycle address (root → leaf hops). The stream re-roots to
  // the leaf hop's dashboard; an inner descendant is just a deeper path.
  path: CyclePath | null;
  intervalMs?: number;
  children: ReactNode;
}) {
  const state = useCycleStreamSource(path, intervalMs);
  return (
    <CycleStreamContext.Provider value={state}>{children}</CycleStreamContext.Provider>
  );
}
