import type { MeteredSpend } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { SPEND_STAT_LABEL, spendStat, type SummaryFacts } from "./campaign-summary";
import { fmtPaired } from "./paired-reading";
import { rowAt } from "./round-candidates";
import { readSpend } from "./spend";

export interface RunSummary {
  cycleId: string;
  state: string;
  nextStep: string;
  rounds: number | null;
  winnerLabel: string | null;
  vsOrigin: string;
  metered: MeteredSpend | null;
  changes: string;
}

export function runSummaryFacts(s: RunSummary): SummaryFacts {
  return {
    title: "Run finished",
    state: s.state,
    stats: [
      { label: "Champion", value: s.winnerLabel ?? "—" },
      { label: "Accuracy", value: s.vsOrigin, sub: "origin → selection" },
      { label: "Rounds", value: s.rounds === null ? "—" : String(s.rounds) },
      s.metered ? spendStat(s.metered) : { label: SPEND_STAT_LABEL, value: "—" },
    ],
  };
}

export function runSummary(dash: DashboardSnapshot | null): RunSummary | null {
  if (!dash?.cycle_id) return null;
  const standing = dash.run_standing;
  const winner = standing?.selection ?? null;
  const changes = winner ? rowAt(dash, winner)?.reading.changes_description : undefined;
  return {
    cycleId: dash.cycle_id,
    state: dash.status.label,
    nextStep: dash.next_step,
    rounds: standing && winner ? standing.rounds_closed : null,
    winnerLabel: winner?.label ?? null,
    vsOrigin: standing ? fmtPaired(standing.vs_origin, "rates") : "—",
    metered: readSpend(dash).metered,
    changes: changes ?? "",
  };
}
