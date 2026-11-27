import {
  type HierarchyState,
  type NextAct,
  type Refusal,
  type RunPhase,
  type StopReading,
  type StopReason,
  RUN_PHASE_INFO,
  STOP_REASON_INFO,
} from "@/lib/api/types";

// served: `RUN_PHASE_INFO` is `domain/phases.py`
export function phaseWalks(runPhase: RunPhase | null | undefined): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase].walks;
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
  "revoke-api-token": "Revoke",
};

/** An act past a stop in the browser's words: the control it presses and the field it fills, or
 * the declared field set, or the file read. */
function actText(act: NextAct): string {
  if (act.reads) return `read ${act.reads}`;
  const field = act.field && (act.value !== "" ? `${act.field} (${act.value})` : act.field);
  return [act.kind && ACT_CONTROL[act.kind], field].filter(Boolean).join(" ");
}

/** *said*, then the acts past it in the browser's words: a stop's or a refusal's. */
export function withActs(said: string, acts: readonly NextAct[]): string {
  const spelled = [...new Set(acts.map(actText))];
  return spelled.length > 0 ? `${said} Next: ${spelled.join("; ")}.`.trim() : said;
}

type RefusalSaid = Readonly<{
  message: Refusal["message"];
  next_acts: readonly NextAct[];
  notes: readonly string[];
}>;

/** A served refusal in the browser's words: its sentence, its acts, then its notes; null where
 * none stands. */
export function refusalText(refusal: RefusalSaid): string;
export function refusalText(refusal: RefusalSaid | null): string | null;
export function refusalText(refusal: RefusalSaid | null): string | null {
  if (refusal === null) return null;
  return [withActs(refusal.message, refusal.next_acts), ...refusal.notes].join(" ");
}

/** A stop as served, then its step and the acts past it the cycle's admission offers its reader
 * (served `stop_acts`) — a pause asked and not yet reached, where the run stops. */
export function stopNote(stop: StopReading, acts: readonly NextAct[]): string {
  if (stop.stop_reason === null) {
    return `${stop.said} — ${refusalText(RUN_PHASE_INFO.paused.refusal) ?? ""}`;
  }
  const next = withActs(STOP_REASON_INFO[stop.stop_reason].next_step, acts);
  return next ? `${stop.said} — ${next}` : stop.said;
}

/** What the run's stop said and what the operator does now; empty where it has not stopped. */
export function runNextStep(state: HierarchyState): string {
  return state.stop === null ? "" : stopNote(state.stop, state.admission.stop_acts);
}
