import type { HierarchyCourse, MeteredSpend } from "@/lib/api/types";
import type { DashboardSnapshot } from "@/lib/poll";
import { fmtPaired } from "./paired-reading";
import { rowAt } from "./round-candidates";
import { readSpend } from "./spend";
import { SPEND_STAT_LABEL, cycleSpendStat, type SummaryFacts } from "./summary-facts";

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
    label: "Run finished",
    state: { status: { label: s.state } },
    stats: [
      { label: "Champion", value: s.winnerLabel ?? "—" },
      { label: "Accuracy", value: s.vsOrigin, sub: "origin → selection" },
      { label: "Rounds", value: s.rounds === null ? "—" : String(s.rounds) },
      s.metered ? cycleSpendStat(s.metered) : { label: SPEND_STAT_LABEL, value: "—" },
    ],
  };
}

// The state word and next step are the COURSE's; the numbers frozen beside them, the dashboard's.
export function runSummary(
  dash: DashboardSnapshot | null,
  course: HierarchyCourse | null,
): RunSummary | null {
  if (!dash?.cycle_id || !course) return null;
  const standing = dash.run_standing;
  const winner = standing?.selection ?? null;
  const changes = winner ? rowAt(dash, winner.key)?.reading.changes_description : undefined;
  return {
    cycleId: dash.cycle_id,
    state: course.state.status.label,
    nextStep: course.state.next_step,
    rounds: standing && winner ? standing.rounds_closed : null,
    winnerLabel: winner?.label ?? null,
    vsOrigin: standing ? fmtPaired(standing.vs_origin, "rates") : "—",
    metered: readSpend(dash).metered,
    changes: changes ?? "",
  };
}
