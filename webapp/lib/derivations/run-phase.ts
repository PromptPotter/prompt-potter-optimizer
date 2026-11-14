import {
  type DashboardState,
  type HierarchyState,
  type LookaheadDepth,
  type NextAct,
  type RunPhase,
  type SampleLookahead,
  type StopReading,
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
  "submit-arms": "Submit arms",
};

/** An act past a stop in the browser's words: the control it presses and the field it fills, or
 * the declared field set, or the file read. */
function actText(act: NextAct): string {
  if (act.reads) return `read ${act.reads}`;
  const field = act.field && (act.value ? `${act.field} (${act.value})` : act.field);
  return [act.kind && ACT_CONTROL[act.kind], field].filter(Boolean).join(" ");
}

/** *said*, then the acts past it in the browser's words: a stop's or a refusal's. */
export function withActs(said: string, acts: readonly NextAct[]): string {
  const spelled = [...new Set(acts.map(actText))];
  return spelled.length > 0 ? `${said} Next: ${spelled.join("; ")}.`.trim() : said;
}

/** A stop as served, then its step and the acts the cycle's admission offers — a pause asked and
 * not yet reached, where the run stops. A campaign that refuses a launch (served
 * `launch_refusal`) offers no `start-run`. */
export function stopNote(stop: StopReading, launchRefusal: string): string {
  if (stop.stop_reason === null) return `${stop.said} — ${RUN_PHASE_INFO.paused.parked}`;
  const info = STOP_REASON_INFO[stop.stop_reason];
  const offered = info.next_acts.filter((act) => !(launchRefusal && act.kind === "start-run"));
  const next = withActs(info.next_step, offered);
  return next ? `${stop.said} — ${next}` : stop.said;
}

/** What the run's stop said and what the operator does now; empty where it has not stopped. */
export function runNextStep(state: HierarchyState): string {
  return state.stop === null ? "" : stopNote(state.stop, state.admission.launch_refusal);
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
