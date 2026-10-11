import type { ServedRound } from "@/lib/api/types";

export interface DegradedRoundNotice {
  round: number;
  detail: string;
}

// Every graded cause serves its sentence (`results_health.py::_suggested_action`).
export function degradedRoundNotices(rounds: readonly ServedRound[] | undefined): DegradedRoundNotice[] {
  const out: DegradedRoundNotice[] = [];
  for (const r of rounds ?? []) {
    if (r.health?.grade !== "degraded" || !r.health.suggested_action) continue;
    out.push({ round: r.round, detail: r.health.suggested_action });
  }
  return out;
}
