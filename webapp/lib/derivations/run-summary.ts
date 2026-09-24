// A finished run's chat-thread item, lifted verbatim off `dashboard.json`. Values, not a pointer:
// a `resume` or rewind moves the dashboard on, and the frozen item must not restate itself.

import type { DashboardSnapshot } from "@/lib/poll";
import { bestObserveTarget } from "./searchPoint";
import { headlineStats } from "./headline-stats";
import { roundHasCandidates, sortedRounds } from "./round-candidates";
import { readSpend } from "./spend";

export interface RunSummary {
  // The frozen item outlives the address in view.
  cycleId: string;
  stopReason: string | null;
  // Rounds closed WITH candidates: an L2/L3-terminal round measured nothing.
  rounds: number;
  championLabel: string | null;
  // `null` where the candidate never covered the parent's panel — an absent floor is not a zero.
  accuracy: number | null;
  parentAccuracy: number | null;
  // Served lift over origin, in logits. Format as θ, never as a percent.
  abilityDelta: number | null;
  usedUsd: number | null;
  changes: string;
  // Lets a champion still at the origin tell "nothing tried" from "tried and lost". Round 0
  // holds no election, so its `improved` is `null`.
  lastRound: {
    round: number;
    candidates: number;
    improved: boolean | null;
    verdictReason: string | null;
  } | null;
}

export function runSummary(dash: DashboardSnapshot | null): RunSummary | null {
  if (!dash?.cycle_id) return null;
  const closed = sortedRounds(dash).filter(roundHasCandidates);
  const target = bestObserveTarget(dash);
  const champion = target
    ? closed.find((r) => r.round === target.round)?.candidates[target.idx]
    : undefined;
  const { abilityDelta } = headlineStats(dash);
  const last = closed.at(-1);
  return {
    cycleId: dash.cycle_id,
    stopReason: dash.stop_reason ?? null,
    rounds: closed.length,
    championLabel: target?.courseLabel ?? null,
    accuracy: champion?.accuracy ?? null,
    parentAccuracy: champion?.matched_parent_accuracy ?? null,
    abilityDelta,
    usedUsd: readSpend(dash).usedUsd,
    changes: champion?.changes_description ?? "",
    lastRound: last
      ? {
          round: last.round,
          candidates: last.candidates.length,
          improved: last.improved,
          verdictReason: last.verdict_reason,
        }
      : null,
  };
}
