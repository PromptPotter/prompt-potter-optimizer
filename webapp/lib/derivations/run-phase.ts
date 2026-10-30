import type {
  DashboardState,
  PauseReading,
  ProducerReading,
  RunPhase,
  RunPhaseInfo,
  RunStatus,
  StatusMarkInfo,
  StopReason,
  WaitingOn,
} from "@/lib/api/types.generated";
import {
  DASHBOARD_STATE_PAUSE_WORDS,
  LIFECYCLE_STATUS_LABELS,
  LOOKAHEAD_WORDS,
  PRODUCER_STATE_INFO,
  RAY_HEAD_WORDS,
  RUN_PHASE_INFO,
  STATUS_MARK_INFO,
  STOP_REASON_INFO,
} from "@/lib/api/types.generated";
import { fmtDuration } from "@/lib/format";

// served: `RUN_PHASE_INFO` is `domain/phases.py`
type PhaseFact = "settled" | "awaits_operator";

export function phaseWalks(runPhase: RunPhase | null | undefined): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase].walks;
}

export function phaseIs(runPhase: RunPhase | null | undefined, fact: PhaseFact): boolean {
  return runPhase != null && RUN_PHASE_INFO[runPhase][fact];
}

export function phaseParked(runPhase: RunPhase, attached: boolean): string {
  const info: RunPhaseInfo = RUN_PHASE_INFO[runPhase];
  return attached ? info.parked_attached : info.parked;
}

export function dockPriority(runPhase: RunPhase): number {
  return RUN_PHASE_INFO[runPhase].dock_priority;
}

export function isStopReason(v: unknown): v is StopReason {
  return typeof v === "string" && v in STOP_REASON_INFO;
}

export type RunPhaseTone = StatusMarkInfo["tone"];

/** A run's one word: `domain/cycle_index.py::RunStatus.label`, the same lookup. */
export function runWord(status: RunStatus): string {
  if (status.mark === "archived") return LIFECYCLE_STATUS_LABELS.archived;
  if (status.run_phase !== "terminal") return RUN_PHASE_INFO[status.run_phase].label;
  return status.stop_reason === null ? "—" : STOP_REASON_INFO[status.stop_reason].label;
}

/** What the operator does now, as the run's stop reason states it; empty where it owes nothing. */
export function runNextStep(status: RunStatus): string {
  return status.stop_reason === null ? "" : STOP_REASON_INFO[status.stop_reason].next_step;
}

/** A pause's next step: its stop reason's, or — asked, not yet reached — where the run stops. */
export function pauseNextStep(pause: PauseReading): string {
  return pause.stop_reason === null
    ? RUN_PHASE_INFO.paused.parked_attached
    : STOP_REASON_INFO[pause.stop_reason].next_step;
}

/** A walking run's head word: its producer's state, or the measuring word on an open cell. */
export function producerWord(producer: ProducerReading): string {
  return producer.state === "idle" && producer.open_for_s !== null
    ? RAY_HEAD_WORDS.measuring
    : PRODUCER_STATE_INFO[producer.state].label;
}

/** The cell an open call holds: its arm, or a pass no arm owns, and its sample. */
export function waitingText(waiting: WaitingOn): string {
  return `${waiting.label ?? RAY_HEAD_WORDS.pass} · ${RAY_HEAD_WORDS.sample} ${waiting.position}`;
}

/** How many more cells the spend limits admit, where money holds the walk. */
export function affordableText(affordable: number): string {
  return affordable <= 0
    ? LOOKAHEAD_WORDS.no_further_cell
    : `${affordable} ${LOOKAHEAD_WORDS.more_affordable}`;
}

/** What a producer's state raises on its own (`silent`, `wedged`); null where nothing is wrong. */
export function producerAlert(producer: ProducerReading): { title: string; detail: string } | null {
  const info = PRODUCER_STATE_INFO[producer.state];
  if (!info.alert) return null;
  const clocked =
    producer.state === "wedged" && producer.silent_for_s !== null
      ? ` for ${fmtDuration(producer.silent_for_s)}`
      : "";
  return { title: info.alert, detail: info.alert_detail + clocked };
}

export function statusTone(status: RunStatus): `tone-${RunPhaseTone}` {
  return `tone-${STATUS_MARK_INFO[status.mark].tone}`;
}

export function phasePauseLabel(
  state: DashboardState | null | undefined,
  optimizerStep: string | null | undefined,
): string {
  if (state == null) return "the current round";
  return (
    (state === "optimizer_step" ? optimizerStep : DASHBOARD_STATE_PAUSE_WORDS[state]) ||
    "the current round"
  );
}
