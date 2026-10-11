import type {
  DashboardState,
  RunPhase,
  RunPhaseInfo,
  RunStatus,
  StopReason,
} from "@/lib/api/types.generated";
import {
  DASHBOARD_STATE_PAUSE_WORDS,
  RUN_PHASE_INFO,
  STOP_REASON_OUTCOMES,
} from "@/lib/api/types.generated";

// served: `RUN_PHASE_INFO` is `domain/phases.py`
type PhaseFact = "settled" | "authoring" | "awaits_operator";

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
  return typeof v === "string" && v in STOP_REASON_OUTCOMES;
}

export type RunPhaseTone = "live" | "attention" | "quiet" | "success" | "warn" | "danger";
export interface RunPhaseMark {
  glyph: string;
  tone: RunPhaseTone;
}

export const STATUS_MARK: Record<RunStatus["mark"], RunPhaseMark> = {
  running: { glyph: "●", tone: "live" },
  starting: { glyph: "◌", tone: "live" },
  queued: { glyph: "…", tone: "quiet" },
  gate: { glyph: "◆", tone: "attention" },
  paused: { glyph: "‖", tone: "quiet" },
  checkin: { glyph: "✎", tone: "quiet" },
  detached: { glyph: "⊘", tone: "warn" },
  success: { glyph: "✓", tone: "success" },
  halted: { glyph: "■", tone: "warn" },
  failed: { glyph: "✕", tone: "danger" },
  archived: { glyph: "▫", tone: "quiet" },
  unknown: { glyph: "?", tone: "quiet" },
};

export function statusTone(status: RunStatus): `tone-${RunPhaseTone}` {
  return `tone-${STATUS_MARK[status.mark].tone}`;
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
