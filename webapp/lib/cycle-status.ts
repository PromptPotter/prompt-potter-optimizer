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
