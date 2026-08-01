import type { DashboardState, RunPhase } from "@/lib/api/types.generated";
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
//
// EVERY map below is keyed on a GENERATED union, never on `string`. That is what
// makes a new backend phase a compile error here instead of a blank render: the
// maps used to be `Record<string, …>`, so a member could be missing and nothing —
// not tsc, not eslint, not a test — could say so. `gate` proved it, sitting in
// `RunPhase` while the dock read `string` and rendered a held origin gate as an
// ordinary run. Regenerate with `python scripts/build_ts_types.py`.

// Terminal is deliberately absent: its label is the STOP REASON, not the phase.
// `Exclude` says so in the type rather than parking a placeholder here.
const RUN_PHASE_LABEL: Record<Exclude<RunPhase, "terminal">, string> = {
  checkin: "Check-in",
  running: "Running",
  paused: "Paused",
  gate: "Origin gate",
  detached: "Detached",
};

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
//
// Total over RunPhase: the three that are not dock entries sort last explicitly,
// which is also what makes this the one place the runtime guard below can derive
// "is this string a phase?" from — no second hand-written list of the members.
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
// THIS one map — the navbar dock, the RemoteBar, the workspace `liveCycles` — so
// they can't disagree.
//
// A booleans-per-phase map rather than a Set, because a Set of three strings can
// go stale in silence: that is exactly how `gate` spent months declared by the
// server and invisible to the dock. Here a new phase does not compile until
// somebody decides whether it is an open unit.
const IN_FLIGHT: Record<RunPhase, boolean> = {
  running: true,
  gate: true,
  paused: true,
  checkin: false,
  detached: false,
  terminal: false,
};

export function isInFlight(runPhase: string | null | undefined): boolean {
  return isRunPhase(runPhase) && IN_FLIGHT[runPhase];
}

// Unknown / absent phases sort last.
export function dockPriority(runPhase: string | null | undefined): number {
  return isRunPhase(runPhase) ? DOCK_PRIORITY[runPhase] : 3;
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
  if (isRunPhase(runPhase) && runPhase !== "terminal") return RUN_PHASE_LABEL[runPhase];
  return runPhase || "—";
}

// Short, human label for the fine-grained activity phase (`dashboard.json::state`),
// used in the pause affordance ("Finishing {…} — will pause"). Distinct register
// from terms.ts (long tooltip sentences) and RUN_PHASE_LABEL (control words).
//
// `null` = no activity worth naming, so the caller's generic phrase reads better
// than a literal one. It is a declared choice per state rather than an omission:
// `init` and `stopped` were emitted by the writer from the start and simply had no
// entry here, which rendered the affordance blank with nothing to notice.
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
