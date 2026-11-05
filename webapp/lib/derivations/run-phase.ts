import {
  type DashboardState,
  type LookaheadDepth,
  type PauseReading,
  type RunPhase,
  type RunStatus,
  type SampleLookahead,
  type StopReason,
  DASHBOARD_STATE_PAUSE_WORDS,
  LOOKAHEAD_WORDS,
  RUN_PHASE_INFO,
  STOP_REASON_INFO,
} from "@/lib/api/types";
import { filled } from "@/lib/format";

// served: `RUN_PHASE_INFO` is `domain/phases.py`
type PhaseFact = "settled" | "awaits_operator";

export function phaseWalks(runPhase: RunPhase | null | undefined): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase].walks;
}

export function phaseIs(runPhase: RunPhase | null | undefined, fact: PhaseFact): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase][fact];
}

export function dockPriority(runPhase: RunPhase): number {
  return RUN_PHASE_INFO[runPhase].dock_priority;
}

export function isStopReason(v: unknown): v is StopReason {
  return typeof v === "string" && v in STOP_REASON_INFO;
}

/** What the operator does now, as the run's stop reason states it; empty where it owes nothing. */
export function runNextStep(status: RunStatus): string {
  return status.stop_reason === null ? "" : STOP_REASON_INFO[status.stop_reason].next_step;
}

/** A pause's next step: its stop reason's, or — asked, not yet reached — where the run stops. */
export function pauseNextStep(pause: PauseReading): string {
  return pause.stop_reason === null
    ? RUN_PHASE_INFO.paused.parked
    : STOP_REASON_INFO[pause.stop_reason].next_step;
}

/** How many more cells the spend limits admit, where money holds the walk. */
export function affordableText(affordable: number): string {
  return affordable <= 0
    ? LOOKAHEAD_WORDS.no_further_cell
    : `${affordable} ${LOOKAHEAD_WORDS.more_affordable}`;
}

/** What the armed look-ahead depth does: the mode in force, its bound and the calls cut so far. */
export function lookaheadExplained(la: SampleLookahead): string {
  if (la.auto_refusal === "one_at_a_time") return LOOKAHEAD_WORDS.one_at_a_time;
  const held = filled(la.auto ? LOOKAHEAD_WORDS.holds_every_round : LOOKAHEAD_WORDS.holds_this_round, la);
  return la.discards > 0 ? `${held} ${filled(LOOKAHEAD_WORDS.discarded, la)}` : held;
}

export function lookaheadDepthLabel(d: LookaheadDepth): string {
  return d.cells === 1 ? LOOKAHEAD_WORDS.depth_one : filled(LOOKAHEAD_WORDS.depth_many, d);
}

export function phasePauseLabel(state: DashboardState, optimizerStep: string | null): string {
  return state === "optimizer_step" && optimizerStep !== null
    ? optimizerStep
    : DASHBOARD_STATE_PAUSE_WORDS[state];
}
