import type { OptimizerFact, ServedDashboard } from "@/lib/api/types";

export interface RoundReading {
  round: number;
  // Round 0 holds no election, so its `improved` is `null`.
  improved: boolean | null;
  verdictReason: string | null;
  facts: OptimizerFact[];
}

// A round's closed reading; the round no file holds yet (`round_axis.unfiled`) has none.
export function roundReading(dash: ServedDashboard | null, round: number): RoundReading | null {
  if (!dash || round === dash.round_axis.unfiled) return null;
  const r = dash.rounds.find((x) => x.round === round);
  if (!r) return null;
  return {
    round: r.round,
    improved: r.improved,
    verdictReason: r.verdict_reason,
    facts: r.optimizer_facts,
  };
}
