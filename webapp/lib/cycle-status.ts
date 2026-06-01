// One canonical campaign-status word. The live dashboard.json carries
// `state` (the FSM phase) plus `stop_reason`; the cycle list / index.json
// carries `status`. For a terminal cycle these disagree — `state` reads
// "stopped" while `status` reads "interrupted" — so the same campaign
// shows two different words across the status bar, TopStrip, and the
// breadcrumb picker. Collapsing `state`+`stop_reason` to the `status`
// vocabulary makes every surface agree.
export function cycleStatusLabel(
  state: string | null | undefined,
  stopReason: string | null | undefined,
): string {
  if (state === "stopped" && stopReason) return stopReason;
  return state || "—";
}

// Terminal FSM phases the optimizer can leave on disk. `_finalize` in
// live_dashboard/view.py writes exactly one (`_set_state("stopped")`) on
// every clean stop, crash, or completion; this is the single place that
// names the closed set, so a new terminal phase is added here once.
const TERMINAL_STATES = new Set(["stopped"]);

// Is the optimizer still executing this cycle? The complement of the
// terminal set — the one predicate every "live"/transient indicator gates
// on. A null/unknown state is treated as running (a just-spawned cycle that
// hasn't written its first phase yet shouldn't read as dead); freshness
// (`isLive`) is the other half of the gate and catches a silently-dead
// producer that never reached a terminal phase.
export function isRunningState(state: string | null | undefined): boolean {
  return !(typeof state === "string" && TERMINAL_STATES.has(state));
}
