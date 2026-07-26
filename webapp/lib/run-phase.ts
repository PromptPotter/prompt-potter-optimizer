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
  detached: "Detached",
};

// The one definition of "this cycle is a live, incomplete unit" — a genuine
// entry in the OS-style dock of open units: running, its origin gate, and
// paused (a suspended, resumable unit). `detached` is deliberately EXCLUDED:
// post-heartbeat it means the producer is dead, not "alive but quiet". The
// in-flight heartbeat (dispatch/llm_call/heartbeat.py, 15 s) bumps the ledger →
// dashboard.json during any long await heartbeated today — the optimizer LLM
// call, an L4 outer cycle awaiting a multi-minute inner campaign, and the
// backend scoring query — so a genuinely-alive cycle can no longer go stale
// past RUN_FRESH_S (30 s). A stale dashboard therefore means the producer
// vanished (crash / kill / sleep); such a cycle is dead and gets reaped to
// terminal, not shown as an open app. Every "is anything running" surface reads
// THIS one set — the navbar dock, the RemoteBar, the workspace `liveCycles` — so
// they can't disagree.
//
// `gate` was a member of this set for a long time without ever arriving: the
// server DECLARES it (there is no `.runtime/` flag, only the runner can know it)
// but `derive_run_phase` never read the declaration, so the cycle list reported
// plain `running`. Worse, the gate's wait wrote nothing, so after 30s it read
// `detached` and left the dock entirely — the operator's cue to decide vanished
// while the run waited on that decision. Both halves are fixed server-side; this
// set was right all along.
const IN_FLIGHT_PHASES = new Set(["running", "paused", "gate"]);

export function isInFlight(runPhase: string | null | undefined): boolean {
  return !!runPhase && IN_FLIGHT_PHASES.has(runPhase);
}

// Dock order, and it sorts by WHAT NEEDS YOU rather than by what is busy.
//
//   gate    — blocked ON THE OPERATOR. It makes no progress until you decide, so
//             every second it is not at the top is a second wasted.
//   running — making progress without you. Interesting, not urgent.
//   paused  — suspended by you; it is where you left it and will wait.
//
// The old order was "executing first", which reads as a status board rather than
// a queue of work. That was harmless only while `gate` was unreachable: the server
// declared it but never derived it, so the cycle list reported an ordinary
// `running` and this list could not distinguish the two. With the derivation fixed
// (`runtime_flags.py::derive_run_phase`), "needs a decision" is finally a state the
// dock can see, and it belongs first.
const DOCK_PRIORITY: Record<string, number> = { gate: 0, running: 1, paused: 2 };

// Unknown / absent phases sort last. Written as an explicit miss-check because the
// terse `(runPhase && DOCK_PRIORITY[runPhase]) ?? 3` does not typecheck under
// `noUncheckedIndexedAccess`: the `&&` yields `"" | number | undefined`, and `??`
// passes `""` straight through as a non-number.
export function dockPriority(runPhase: string | null | undefined): number {
  if (!runPhase) return 3;
  const p = DOCK_PRIORITY[runPhase];
  return p === undefined ? 3 : p;
}

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
