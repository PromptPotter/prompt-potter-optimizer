// Decision derivation — the chat's in-thread control surface. This is where the
// parallel chat ↔ cycle-trace streams MERGE: the optimizer holds at a gate, the
// chat raises the choice inline, and the operator's click fires an existing
// `/commands/{kind}` verb that lands on the cycle ledger.
//
// Scope (Arc 1): the ORIGIN GATE only. The gate decision moved into the chat as
// the canonical home (the old global `OriginGateModal` — mis-mounted inside the
// hard-samples heatmap — is deleted; its rich verdict is folded in here). The
// paused/running pause-resume-stop controls stay on the always-present
// RemoteControl, which is present on every tab — not duplicated in-thread.

import type { DashboardSnapshot } from "@/lib/poll";
import type { OriginGateDecision } from "@/lib/api";
import type { DegradationHealth } from "@/lib/api/types";
import { roundHealthAt } from "@/lib/derivations";

interface DecisionButton {
  decision: OriginGateDecision;
  label: string;
  variant: "primary" | "ghost" | "danger";
}

export interface DecisionItem {
  kind: "origin-gate";
  title: string;
  lead: string;
  // The origin verdict the gate holds on — round 0's backend-computed `health`
  // block, the generated type (no loose re-parse; R-36: served, never recomputed).
  verdict: DegradationHealth | null;
  buttons: DecisionButton[];
}

const GATE_BUTTONS: DecisionButton[] = [
  { decision: "rescore", label: "Re-score origin", variant: "primary" },
  { decision: "proceed", label: "Proceed anyway", variant: "ghost" },
  { decision: "abort", label: "Abort", variant: "danger" },
];

// Pure: the current decision item for the viewed cycle, or `null` when no
// operator choice is pending. Cleared automatically the instant `run_phase`
// leaves `gate` (the runner side-effect resolves; the poll observes it).
export function deriveDecision(
  runPhase: string | null | undefined,
  dash: DashboardSnapshot | null,
): DecisionItem | null {
  if (runPhase !== "gate") return null;
  const verdict = roundHealthAt(dash, 0);
  const grade = verdict?.grade ?? "unknown";
  return {
    kind: "origin-gate",
    title: `Origin gate — verdict: ${grade}`,
    lead: `The origin (round 0) scored ${grade} — not healthy enough to optimize against. The run is holding before L1. Fix the connector and re-score to re-check, proceed to optimize anyway, or abort.`,
    verdict,
    buttons: GATE_BUTTONS,
  };
}
