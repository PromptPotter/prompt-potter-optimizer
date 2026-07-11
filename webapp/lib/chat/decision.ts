// Decision derivation — the chat's in-thread control surface. This is where the
// parallel chat ↔ cycle-trace streams MERGE: the optimizer holds at a gate, the
// chat raises the choice inline, and the operator's click fires an existing
// `/commands/{kind}` verb that lands on the cycle ledger.
//
// Scope (Arc 1): the ORIGIN GATE only. The gate decision moved into the chat as
// the canonical home (the old global `OriginGateModal` — mis-mounted inside the
// hard-samples heatmap — is deleted; its rich verdict is folded in here). The
// paused/running pause-resume-stop controls stay on the always-present RemoteBar
// pill (cross-tab ambient remote) — not duplicated in-thread.

import type { DashboardSnapshot } from "@/lib/poll";
import type { OriginGateDecision } from "@/lib/api";

interface DecisionButton {
  decision: OriginGateDecision;
  label: string;
  variant: "primary" | "ghost" | "danger";
}

// The origin verdict the gate holds on — the `health` block of round 0
// (`dashboard.json::rounds[round==0].health`), the same shape the deleted modal
// read. Loose by design: dashboard.json is operator-facing JSON.
export interface GateVerdict {
  grade: string;
  degradedRate?: number;
  structuralCount?: number;
  transientCount?: number;
  dominantNode?: string;
  suggestedAction?: string;
  reasons: string[];
}

export interface DecisionItem {
  kind: "origin-gate";
  title: string;
  lead: string;
  verdict: GateVerdict | null;
  buttons: DecisionButton[];
}

const GATE_BUTTONS: DecisionButton[] = [
  { decision: "rescore", label: "Re-score origin", variant: "primary" },
  { decision: "proceed", label: "Proceed anyway", variant: "ghost" },
  { decision: "abort", label: "Abort", variant: "danger" },
];

function asRec(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" ? (v as Record<string, unknown>) : {};
}
function num(v: unknown): number | undefined {
  return typeof v === "number" && Number.isFinite(v) ? v : undefined;
}
function str(v: unknown): string | undefined {
  return typeof v === "string" ? v : undefined;
}

function readVerdict(dash: DashboardSnapshot | null): GateVerdict | null {
  const rounds: Record<string, unknown>[] = Array.isArray(dash?.rounds)
    ? (dash.rounds as unknown as Record<string, unknown>[])
    : [];
  const origin = rounds.find((r) => num(r.round) === 0);
  const health = origin ? asRec(origin.health) : null;
  if (!health || Object.keys(health).length === 0) return null;
  const reasons = Array.isArray(health.reasons)
    ? health.reasons.filter((r): r is string => typeof r === "string")
    : [];
  return {
    grade: str(health.grade) ?? "unknown",
    degradedRate: num(health.degraded_rate),
    structuralCount: num(health.structural_count),
    transientCount: num(health.transient_count),
    dominantNode: str(health.dominant_node),
    suggestedAction: str(health.suggested_action),
    reasons,
  };
}

// Pure: the current decision item for the viewed cycle, or `null` when no
// operator choice is pending. Cleared automatically the instant `run_phase`
// leaves `gate` (the runner side-effect resolves; the poll observes it).
export function deriveDecision(
  runPhase: string | null | undefined,
  dash: DashboardSnapshot | null,
): DecisionItem | null {
  if (runPhase !== "gate") return null;
  const verdict = readVerdict(dash);
  const grade = verdict?.grade ?? "unknown";
  return {
    kind: "origin-gate",
    title: `Origin gate — verdict: ${grade}`,
    lead: `The origin (round 0) scored ${grade} — not healthy enough to optimize against. The run is holding before L1. Fix the connector and re-score to re-check, proceed to optimize anyway, or abort.`,
    verdict,
    buttons: GATE_BUTTONS,
  };
}
