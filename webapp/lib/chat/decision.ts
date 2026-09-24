// The chat's in-thread decision: the ORIGIN GATE, whose home is here. Run controls stay on
// RemoteControl, present on every tab, and are never duplicated in-thread.

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
  verdict: DegradationHealth | null;
  buttons: DecisionButton[];
}

const GATE_BUTTONS: DecisionButton[] = [
  { decision: "rescore", label: "Re-score origin", variant: "primary" },
  { decision: "proceed", label: "Proceed anyway", variant: "ghost" },
  { decision: "abort", label: "Abort", variant: "danger" },
];

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
