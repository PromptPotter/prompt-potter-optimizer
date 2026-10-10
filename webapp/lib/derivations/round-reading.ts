import type { OptimizerFact } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";

export interface RoundReading {
  round: number;
  // Round 0 holds no election, so its `improved` is `null`.
  improved: boolean | null;
  verdictReason: string | null;
  facts: OptimizerFact[];
}

export function roundReading(dash: DashboardSnapshot | null, round: number): RoundReading | null {
  if (!dash?.round_axis.completed.includes(round)) return null;
  const r = dash.rounds.find((x) => x.round === round);
  if (!r) return null;
  return {
    round: r.round,
    improved: r.improved,
    verdictReason: r.verdict_reason,
    facts: r.optimizer_facts,
  };
}
