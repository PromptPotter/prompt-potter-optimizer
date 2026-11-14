import {
  type DashboardState,
  type HierarchyState,
  type LookaheadDepth,
  type NextAct,
  type PauseReading,
  type RunPhase,
  type SampleLookahead,
  type StopReason,
  DASHBOARD_STATE_PAUSE_WORDS,
  LOOKAHEAD_WORDS,
  RUN_PHASE_INFO,
  STOP_REASON_INFO,
} from "@/lib/api/types";
import { filled } from "@/lib/format";

// served: `RUN_PHASE_INFO` is `domain/phases.py`
export function phaseWalks(runPhase: RunPhase | null | undefined): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase].walks;
}

export function dockPriority(runPhase: RunPhase): number {
  return RUN_PHASE_INFO[runPhase].dock_priority;
}

export function isStopReason(v: unknown): v is StopReason {
  return typeof v === "string" && v in STOP_REASON_INFO;
}

// The control that presses each act kind; tsc refuses a kind `NextAct` gains and this omits.
const ACT_CONTROL: Record<Exclude<NextAct["kind"], "">, string> = {
  "start-run": "Resume run",
  "set-limits": "Run limits",
  "verify-candidate": "Verify",
  "mint-campaign": "New campaign",
  "fork-cycle": "Steer-fork",
};

/** An act past a stop in the browser's words: the control it presses and the field it fills, or
 * the declared field set, or the file read. */
function actText(act: NextAct): string {
  if (act.reads) return `read ${act.reads}`;
  const field = act.field && (act.value ? `${act.field} (${act.value})` : act.field);
  return [act.kind && ACT_CONTROL[act.kind], field].filter(Boolean).join(" ");
}

/** A stop's step and the acts the cycle's admission offers, as one line: a campaign that
 * refuses a launch (served `launch_refusal`) offers no `start-run`. */
function stopNextStep(reason: StopReason, launchRefusal: string): string {
  const info = STOP_REASON_INFO[reason];
  const offered = info.next_acts.filter((act) => !(launchRefusal && act.kind === "start-run"));
  const acts = [...new Set(offered.map(actText))];
  return acts.length > 0 ? `${info.next_step} Next: ${acts.join("; ")}.`.trim() : info.next_step;
}

/** What the operator does now, as the run's stop reason states it; empty where it owes nothing. */
export function runNextStep(state: HierarchyState): string {
  const reason = state.status.stop_reason;
  return reason === null ? "" : stopNextStep(reason, state.admission.launch_refusal);
}

/** A pause's next step: its stop reason's, or — asked, not yet reached — where the run stops. */
export function pauseNextStep(pause: PauseReading, launchRefusal: string): string {
  return pause.stop_reason === null
    ? RUN_PHASE_INFO.paused.parked
    : stopNextStep(pause.stop_reason, launchRefusal);
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
