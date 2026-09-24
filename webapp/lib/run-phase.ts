import type { DashboardState, RunPhase, StopOutcome } from "@/lib/api/types.generated";
import {
  STOP_REASON_LABELS,
  STOP_REASON_NEXT_STEPS,
  STOP_REASON_OUTCOMES,
} from "@/lib/api/types.generated";

// The one display mapping for the served `run_phase`. Every map is keyed on a GENERATED union,
// never `string`, so a new backend phase is a compile error here rather than a blank render.

const RUN_PHASE_LABEL: Record<Exclude<RunPhase, "terminal">, string> = {
  checkin: "Check-in",
  running: "Running",
  paused: "Paused",
  gate: "Origin gate",
  detached: "Detached",
};

// Dock order sorts by what needs the operator: `gate` is blocked on them, so it leads. Keep it
// total — `isRunPhase` derives membership from these keys.
const DOCK_PRIORITY: Record<RunPhase, number> = {
  gate: 0,
  running: 1,
  paused: 2,
  checkin: 3,
  detached: 3,
  terminal: 3,
};

function isRunPhase(v: string | null | undefined): v is RunPhase {
  return !!v && v in DOCK_PRIORITY;
}

// `gate` holds a live process; `paused` has exited; `detached` means the producer is DEAD, since
// the in-flight heartbeat (`dispatch/llm_call/heartbeat.py`) keeps every live cycle fresh.
const HAS_PRODUCER: Record<RunPhase, boolean> = {
  running: true,
  gate: true,
  paused: false,
  checkin: false,
  detached: false,
  terminal: false,
};

// A paused cycle is not in flight: the dock's absence IS the all-quiet signal.
export function hasLiveProducer(runPhase: string | null | undefined): boolean {
  return isRunPhase(runPhase) && HAS_PRODUCER[runPhase];
}

// `none` at the gate (the chat decides), in check-in (ingest owns Start), and with no phase yet
// (a warming cycle's launch is already in flight).
export type RunAction = "pause" | "resume" | "start" | "none";

const PHASE_ACTION: Record<RunPhase, RunAction> = {
  running: "pause",
  paused: "resume",
  // Both relaunch from the last completed round; there is no in-place unpause.
  detached: "start",
  terminal: "start",
  gate: "none",
  checkin: "none",
};

export function runPhaseAction(runPhase: string | null | undefined): RunAction {
  return isRunPhase(runPhase) ? PHASE_ACTION[runPhase] : "none";
}

export function dockPriority(runPhase: string | null | undefined): number {
  return isRunPhase(runPhase) ? DOCK_PRIORITY[runPhase] : 3;
}

// `reason` is `dash.stop_reason` on the live view, the entry's `status` on the cycle list.
export function runPhaseLabel(
  runPhase: string | null | undefined,
  reason: string | null | undefined,
): string {
  if (runPhase === "terminal") {
    return (reason && STOP_REASON_LABELS[reason]) || reason || "Finished";
  }
  if (isRunPhase(runPhase) && runPhase !== "terminal") return RUN_PHASE_LABEL[runPhase];
  return runPhase || "—";
}

// The glyph form of `runPhaseLabel`; the word still rides the mark's `aria-label`.
export type RunPhaseTone = "live" | "attention" | "quiet" | "success" | "warn" | "danger";
export interface RunPhaseMark {
  glyph: string;
  tone: RunPhaseTone;
}

const PHASE_MARK: Record<Exclude<RunPhase, "terminal">, RunPhaseMark> = {
  running: { glyph: "●", tone: "live" },
  gate: { glyph: "◆", tone: "attention" },
  paused: { glyph: "‖", tone: "quiet" },
  checkin: { glyph: "✎", tone: "quiet" },
  detached: { glyph: "⊘", tone: "warn" },
};

const OUTCOME_MARK: Record<StopOutcome, RunPhaseMark> = {
  success: { glyph: "✓", tone: "success" },
  paused: { glyph: "‖", tone: "quiet" },
  halted: { glyph: "■", tone: "warn" },
  failed: { glyph: "✕", tone: "danger" },
};

const UNKNOWN_MARK: RunPhaseMark = { glyph: "?", tone: "quiet" };

export function runPhaseMark(
  runPhase: string | null | undefined,
  reason: string | null | undefined,
): RunPhaseMark {
  if (runPhase === "terminal") {
    const outcome = reason ? STOP_REASON_OUTCOMES[reason] : undefined;
    return outcome ? OUTCOME_MARK[outcome] : UNKNOWN_MARK;
  }
  return isRunPhase(runPhase) && runPhase !== "terminal" ? PHASE_MARK[runPhase] : UNKNOWN_MARK;
}

// Served, never composed here: the browser advises what the terminal and `review.md` advise.
export function stopReasonNextStep(reason: string | null | undefined): string {
  return (reason && STOP_REASON_NEXT_STEPS[reason]) || "";
}

// The pause affordance's word for `dashboard.json::state`; `null` = nothing worth naming, so the
// caller's generic phrase reads instead.
const PHASE_PAUSE_LABEL: Record<DashboardState, string | null> = {
  origin: "scoring origin",
  scoring: "scoring samples",
  between_samples: "scoring samples",
  between_candidates: "scoring samples",
  l1_generate: "generating candidates",
  l2_refining: "refining strategy",
  l3_replanning: "replanning",
  escalation: "escalating",
  init: "starting up",
  stopped: null,
};

export function phasePauseLabel(state: string | null | undefined): string {
  const named = state && state in PHASE_PAUSE_LABEL
    ? PHASE_PAUSE_LABEL[state as DashboardState]
    : null;
  return named || "the current round";
}
