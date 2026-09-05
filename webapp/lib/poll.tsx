"use client";
// Single live-stream store. One provider, one timer, TWO reads of one ledger: the
// chronology (`/ray` — what happened, in order) and the dashboard (a FOLD of that same
// ledger). Consumers subscribe via `useCycleStream()`; the chronology via `useTimeRay()`.
// The dashboard's `rounds[]` summary block is the sole "completed rounds" surface —
// round_NNNN.json is deep-audit only and fetched lazily via `useRoundFile`.
//
// They share a timer because they share a FILE: on unrelated cadences they drift, and any
// surface reading one against the other reports a disagreement that is not on disk.
//
// The viewed MOMENT (`at`) is the seam. A ray step names a physical offset in the leaf cycle's
// ledger, and handing it to the dashboard route replays the fold to that point, so scrubbing the
// chronology moves every panel on the page. `null` is the head.

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
import { liveCandidateId } from "@/lib/candidate-label";
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
import { usePoll } from "./hooks/usePoll";
import { bumpRevalidation, useRevalidation } from "./revalidate";
import { hasLiveProducer } from "./run-phase";
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
// projection of the round in progress. Every consumer drills into this same path;
// `liveL1Candidates` narrows once so the call sites don't repeat the `as Record<...>` cast.

export interface LiveCandidate {
  idx?: number;
  label?: string;
  // ONE shape, whatever the round's state — the served row, already graded by the producer,
  // so no reader re-derives a verdict the row states. It was a string tape here and dicts in
  // the audit twin, which is what forced the browser to regex a rendering.
  samples?: DashboardSample[];
  // The same rows rendered for the operator reading the file. No component reads it; it is
  // declared so the folder-UI half of this block is visible to anyone typing `dash.`.
  sample_lines?: string[];
  // WHY a candidate has no samples — the scoring node's own account of a validation rejection,
  // and the only place it is served. It is NOT copied onto `current_round.candidates`: that row
  // carries the `invalid` FLAG (which every surface reads), while the reasons stay here, where
  // one panel reads them. Serving the list twice would put the same failures in `dashboard.json`
  // in two places, which is the ledger duplication `infrastructure/CLAUDE.md` § Persistence
  // measures. `invalid` is mirrored here only so this half is readable on its own.
  invalid?: boolean;
  validation_failures?: ValidationFailure[];
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

// Match a candidate back to its live slot BY LABEL — the one key both id spaces carry.
//
// A live row has no lineage id until `candidate_scored` stamps one, while every SELECTION is
// minted off the served tree and carries that id — so neither id space spans both halves, and a
// key from either resolves in one only. `label` is canonical (`candidate_label(round, idx)`,
// composed at mint), unique within a round, and already what the HISTORICAL join uses
// (`searchPoint.ts`, `RoundBuffer.stamp_fit`) — so it is the join, not a third id.
function matchLiveCandidate<T extends { label?: string }>(
  candidates: readonly T[],
  label: string,
): T | null {
  if (!label) return null;
  return candidates.find((c) => c.label === label) ?? null;
}

// Output-candidate slot — the sample tape. For a candidate's numbers, `liveCandidateRow`.
export const liveCandidate = (
  dash: DashboardSnapshot | null,
  label: string,
): LiveCandidate | null => matchLiveCandidate(liveL1Candidates(dash), label);

// The in-flight row's NUMBERS, by the same label its tape and its seed resolve on.
export function liveCandidateRow(
  dash: DashboardSnapshot | null,
  label: string,
): DashboardCandidate | null {
  return matchLiveCandidate(liveCandidates(dash), label);
}

// Input-candidate slot — the seed-able prompt_fields / resolved_pipeline_params
// half, for steer-fork seeding from a still-in-flight candidate.
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
  // Three orthogonal axes at once: the run declares `run_phase === "running"`, the connection is
  // fresh, AND the page is showing the head rather than a replayed moment. The single gate for
  // every transient indicator — which is why the moment belongs in it: a past moment has no
  // in-flight anything, so they all go quiet without a panel testing `at` for itself. Composed
  // once, in `useCycleStreamSource`; consumers never re-derive it.
  isLive: boolean;
  // `dash.state` lifted to the top level so transient indicators can gate on
  // the phase (e.g. only blink a sample row when `phase === "scoring"`).
  phase: string | null;
  // The moment `dash` is OF: a physical offset in the leaf cycle's own ledger, or
  // null for the head. Set by scrubbing the time-ray; part of the address, so it
  // clears with it. Panels that name what they are showing read it; the rest do
  // not have to, because it is already folded into `isLive`.
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

// How many chronology records one window carries.
const RAY_WINDOW = 200;

// The two reads sharing the loop. Module-scoped so the identity is stable — `usePoll` reads
// it fresh each tick and must not see a new array every render.
const POLL_KEYS = (): readonly string[] => ["dash", "ray"];

// Two windows, two lifetimes: the HEAD is refetched every tick (new events land there, its
// ETag rides the family's mtimes); OLDER windows are fetched on demand and held, since a
// deeper page is strictly below the cursor by construction and the head never overlaps it.
// No SSE join, no `since=` on the tail: one live channel, one history channel
// (webapp/CLAUDE.md § "/ray is the CHRONOLOGY").
interface RayWindows {
  // Windows older than the head, oldest-first, already concatenated in order.
  older: RayItem[];
  head: RayItem[];
  // Cursor for the window before everything loaded — null at the family's beginning.
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
  /** The whole loaded span, oldest-first. */
  items: RayItem[];
  loaded: boolean;
  failed: boolean;
  /** Older records exist below what is loaded. */
  hasMore: boolean;
  loadOlder: () => void;
  /** Wall clock, advanced once per poll tick — the input the head's age test needs. A pure
   *  derivation cannot call `Date.now()`, and a 304 re-renders nothing, so without this the
   *  "no progress for Xm" reading would freeze at whatever it said when progress stopped. */
  nowMs: number;
  /** Move the viewed moment: an offset in THIS course's ledger replays every dashboard panel
   *  to it, null returns them to the head. A step belonging to a fork or an inner run is an
   *  address rather than a moment — those ledgers have their own offsets, and mixing the two
   *  spaces is the confusion `infrastructure/ledger.py::iter` documents on the writing side. */
  setAt: (offset: number | null) => void;
}

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

// The chronology rides its OWN context off the same provider. Not a style choice: the ray
// window changes identity on a different beat from `dash`, and folding both into one value
// would re-render every memoized chart on a poll that only moved the strip
// (webapp/CLAUDE.md § Render-cost guards).
const TimeRayContext = createContext<TimeRayState | null>(null);

export function useTimeRay(): TimeRayState {
  const v = useContext(TimeRayContext);
  if (!v) {
    throw new Error("useTimeRay must be called inside <CycleStreamProvider>");
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
): { stream: CycleStreamState; ray: TimeRayState } {
  const [state, setState] = useState<CycleStreamState>(INITIAL_STATE);
  const [ray, setRay] = useState<RayWindows>(EMPTY_RAY);
  const [nowMs, setNowMs] = useState(() => Date.now());
  // The viewed MOMENT. Kept beside the dashboard state rather than inside it because the
  // tick writes that and only the operator writes this.
  const [at, setAtState] = useState<number | null>(null);
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
  // The viewed moment, same reason. Written by `setAt` and by the unit-key guard.
  const atRef = useRef<number | null>(null);
  // The head window's ETag, stamped WITH the key it belongs to. Stamping rather than
  // clearing keeps this out of the render phase (`react-hooks/refs`): a mismatched stamp
  // reads as "no validator", and only a tick ever writes. Replaying a stale ETag would
  // 304 into an empty window.
  const rayEtagRef = useRef<{ key: string | null; etag: string | null }>({
    key: null,
    etag: null,
  });
  const loadingOlderRef = useRef(false);

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
    // A moment is an offset into ONE cycle's ledger, so it means nothing anywhere else —
    // carrying it across a switch would replay the new course to a position it may not
    // even have reached.
    atRef.current = null;
    setAtState(null);
    // Identity changed — hard-reset every cycle-scoped field so the prior
    // unit's dash snapshot, chronology and `● Live` badge can't linger for a
    // frame while the first poll of the new unit is in flight.
    setState({ ...INITIAL_STATE, statusText: "Switching to active campaign…" });
    setRay(EMPTY_RAY);
    setRevalCount((c) => c + 1);
  }

  // Moving the moment re-addresses the dashboard read, so it clears that read's validator:
  // the same file mtime answers "the head" and "offset N" differently, and replaying the
  // stamp from the head would 304 the fold away before it was ever fetched.
  const setAt = useCallback((offset: number | null) => {
    atRef.current = offset;
    lastModifiedRef.current = null;
    setAtState(offset);
    setRevalCount((c) => c + 1);
  }, []);

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

  // The dashboard half of the tick. `usePoll` owns the interval, the hidden-tab
  // pause, and the per-key AbortController; this fetches dashboard.json (via
  // If-Modified-Since so unchanged ticks 304 cheaply) and guards its
  // identity stamp. The completed-round summary block rides this same
  // payload (`dash.rounds[]`) — no second fetch path.
  const tickDash = async (signal: AbortSignal) => {
    const id = cycleRef.current;
    const cmp = campaignRef.current;
    const p = pathRef.current;
    if (!id || !cmp || !p) return;
    try {
      // One fetch for any depth: `fetchDashboardByPath` hits the root cycle's
      // dashboard route, riding `?descend=` for inner descendants. `cmp`/`id` are
      // the LEAF stamp ids the payload must self-report (inner ids when
      // descended), so the identity guard below is depth-agnostic. `at` asks the
      // same route for a past moment — the fold replayed to that offset, which the
      // server rebuilds from the ledger rather than serving the materialized head.
      const resp = await fetchDashboardByPath(p, lastModifiedRef.current, signal, atRef.current);
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

  // The chronology half. It reports no `gone` of its own: the dashboard read is this
  // address's authoritative existence oracle (webapp/CLAUDE.md § Failure handling), it
  // already stops the shared loop, and a second voter would only be a second chance to
  // kill a live view.
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
        // A refetch replaces the HEAD only: the head can only have grown at its newest
        // end, and the cursor stays whatever the oldest loaded window reported — a fresh
        // head window's cursor describes a boundary already paged past.
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

  // One timer, two keys. `usePoll` gives each its own AbortController and skips only the
  // key still in flight, so a slow window fetch never delays the dashboard and neither
  // holds the other to its own duration.
  const tick = (signal: AbortSignal, key: string): Promise<void> => {
    if (key === "ray") return tickRay(signal);
    setNowMs(Date.now());
    return tickDash(signal);
  };

  // Three cadences. Reachable at the head → the responsive `intervalMs` (2 s, mostly
  // 304-cheap; user actions fire an immediate tick via `revalidateOn`). Offline → a steady
  // 5 s reconnect probe, so a downed API recovers within ~5 s without hammering. REPLAYING
  // → the same 5 s, because a past moment cannot change: the fold at an offset is immutable
  // and only `run_phase` still moves, so re-folding a growing ledger at 2 s would buy
  // nothing but server work. `usePoll` restarts its timer when this changes.
  const effectiveInterval =
    state.status === "offline" || at !== null ? RECONNECT_INTERVAL_MS : intervalMs;
  usePoll(tick, {
    intervalMs: effectiveInterval,
    keys: POLL_KEYS,
    // A confirmed `gone` STOPS the loop — there is nothing to reconnect to, and a
    // retry cadence would be a lie about the state. The unit-key guard resets
    // `status` on any address change, so the poll re-arms the moment the view
    // moves somewhere real (including the unpin this verdict just triggered).
    enabled: !!path && authed && state.status !== "gone",
    // A mutation elsewhere (fork / stop / cleanup) re-ticks both halves at once, which is
    // the point of sharing the loop: the chronology and the fold move together or they
    // disagree about a run neither of them is wrong about.
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

  // The moment joins the tick's two axes HERE rather than inside it: the tick knows what the
  // server said, this knows what the page is showing.
  //
  // It also OVERRIDES the age reading, and that is the point rather than a wrinkle. A fold
  // stamps `wallclock_serialized_at` at the instant it is composed, so a replayed moment
  // arrives looking a second old and the banner would read "Live · last write 0s ago" over a
  // dashboard showing an hour-old round. The freshness question does not apply: nothing was
  // written, a moment was rebuilt, and the banner has to say which of the two it is.
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
  const { stream, ray } = useCycleStreamSource(path, intervalMs);
  return (
    <CycleStreamContext.Provider value={stream}>
      <TimeRayContext.Provider value={ray}>{children}</TimeRayContext.Provider>
    </CycleStreamContext.Provider>
  );
}
