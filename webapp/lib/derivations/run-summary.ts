// A finished run's chat-thread item, lifted verbatim off `dashboard.json`. Values, not a pointer:
// a `resume` or rewind moves the dashboard on, and the frozen item must not restate itself.

import type { MeteredSpend, OptimizerFact } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPct0 } from "@/lib/format";
import { runPhaseLabel } from "@/lib/run-phase";
import { SPEND_STAT_LABEL, spendStat, type SummaryFacts } from "./campaign-summary";
import { bestObserveTarget } from "./searchPoint";
import { headlineStats } from "./headline-stats";
import { roundHasCandidates } from "./round-candidates";
import { readSpend } from "./spend";

export interface RunSummary {
  // The frozen item outlives the address in view.
  cycleId: string;
  stopReason: DashboardSnapshot["stop_reason"];
  // Rounds closed WITH candidates: a round closed before its measurement measured nothing.
  rounds: number;
  championLabel: string | null;
  // `null` where the candidate never covered the parent's panel — an absent floor is not a zero.
  accuracy: number | null;
  parentAccuracy: number | null;
  // The bench's served held-out lift of the pick over the origin, in the served headline column.
  benchLift: number | null;
  metered: MeteredSpend | null;
  changes: string;
  // Lets a champion still at the origin tell "nothing tried" from "tried and lost". Round 0
  // holds no election, so its `improved` is `null`.
  lastRound: {
    round: number;
    improved: boolean | null;
    verdictReason: string | null;
    // The optimizer's own words about that round, whichever optimizer ran it.
    facts: OptimizerFact[];
  } | null;
}

// The frozen item as `shell/SummaryBlock` draws it. θ does not appear: a log line has no hover to
// hide jargon behind.
export function runSummaryFacts(s: RunSummary): SummaryFacts {
  return {
    title: "Run finished",
    state: s.stopReason ? runPhaseLabel("terminal", s.stopReason) : null,
    stats: [
      { label: "Champion", value: s.championLabel ?? "—" },
      {
        label: "Accuracy",
        value: fmtPct0(s.accuracy),
        sub:
          s.accuracy != null && s.parentAccuracy != null
            ? `from ${fmtPct0(s.parentAccuracy)}`
            : undefined,
      },
      { label: "Rounds", value: String(s.rounds) },
      s.metered ? spendStat(s.metered) : { label: SPEND_STAT_LABEL, value: "—" },
    ],
  };
}

export function runSummary(dash: DashboardSnapshot | null): RunSummary | null {
  if (!dash?.cycle_id) return null;
  const closed = (dash.rounds ?? []).filter(roundHasCandidates);
  const target = bestObserveTarget(dash);
  const champion = target
    ? closed.find((r) => r.round === target.round)?.candidates[target.idx]
    : undefined;
  const { benchLift } = headlineStats(dash);
  const last = closed.at(-1);
  return {
    cycleId: dash.cycle_id,
    stopReason: dash.stop_reason ?? null,
    rounds: closed.length,
    championLabel: target?.courseLabel ?? null,
    accuracy: champion?.accuracy ?? null,
    parentAccuracy: champion?.reference_accuracy ?? null,
    benchLift,
    metered: readSpend(dash).metered,
    changes: champion?.changes_description ?? "",
    lastRound: last
      ? {
          round: last.round,
          improved: last.improved,
          verdictReason: last.verdict_reason,
          // Guarded: `dashboard.json` is served verbatim, and a file an older build wrote lacks it.
          facts: Array.isArray(last.optimizer_facts) ? last.optimizer_facts : [],
        }
      : null,
  };
}
