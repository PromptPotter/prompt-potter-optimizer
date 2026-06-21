import { STOP_REASON_LABELS } from "@/lib/api/types.generated";

// One display mapping for the run-state vocabulary (RunPhase), read off the
// single `run_phase` field the backend computes. Replaces the old
// `cycleStatusLabel`, which existed only to reconcile dashboard.json's `state` +
// `stop_reason` against the cycle list's `status` — two vocabularies that
// disagreed (a terminal cycle read "stopped" on one surface and "interrupted" on
// another). Now every surface reads `run_phase`; this collapses it to one word.
//
// The terminal reason renders through STOP_REASON_LABELS — the generated mirror
// of domain/phases.py::STOP_REASON_INFO (the single label source, no drift).

const RUN_PHASE_LABEL: Record<string, string> = {
  checkin: "Check-in",
  running: "Running",
  paused: "Paused",
  gate: "Origin gate",
  stopping: "Stopping",
  detached: "Detached",
};

// Live view: reason lives in `dash.stop_reason`. Cycle list: reason lives in the
// entry's `status` (the precise StopReason value, "active" while running). Pass
// whichever the surface has.
export function runPhaseLabel(
  runPhase: string | null | undefined,
  reason: string | null | undefined,
): string {
  if (runPhase === "terminal") {
    return (reason && STOP_REASON_LABELS[reason]) || reason || "Finished";
  }
  return (runPhase && RUN_PHASE_LABEL[runPhase]) || runPhase || "—";
}

// Short, human label for the fine-grained activity phase (`dashboard.json::state`),
// used in the pause affordance ("Finishing {…} — will pause"). Distinct register
// from terms.ts (long tooltip sentences) and RUN_PHASE_LABEL (control words).
const PHASE_PAUSE_LABEL: Record<string, string> = {
  origin: "scoring origin",
  scoring: "scoring samples",
  between_samples: "scoring samples",
  between_candidates: "scoring samples",
  l1_generate: "generating candidates",
  l2_refining: "refining strategy",
  l3_replanning: "replanning",
  escalation: "escalating",
};

export function phasePauseLabel(state: string | null | undefined): string {
  return (state && PHASE_PAUSE_LABEL[state]) || "the current round";
}

// The connection-aware run phase for the *live single-cycle* view. The backend
// declares running/paused/stopping/terminal into dashboard.json; only `running`
// is ambiguous to a viewer whose poll has gone quiet — a fresh producer is
// running, a silent one is `detached` (the same value the cycle-list's
// derive_run_phase emits). paused/stopping/terminal are declared truths the
// connection can't override. Computed once in poll.tsx; surfaces read the result.
export function resolveRunPhase(
  runPhase: string | null | undefined,
  connectionLive: boolean,
): string | null {
  if (!runPhase) return null;
  if (runPhase === "running") return connectionLive ? "running" : "detached";
  return runPhase;
}
