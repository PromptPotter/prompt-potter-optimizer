import { liveCandidates, roundOf } from "@/lib/poll";
import type { ServedDashboard } from "@/lib/api";
import type { ArmReading } from "@/lib/api/types";
import type { ArmRow, RoundCandidates, RoundView } from "@/lib/types";

export function scoreboardReading(doc: RoundView | null, key: string): ArmReading | null {
  return doc?.scoreboard.find((r) => r.reading.arm.key === key)?.reading ?? null;
}

export function roundCandidates(dash: ServedDashboard | null): ArmRow[] {
  const out: ArmRow[] = [];

  for (const r of dash?.rounds ?? []) {
    r.candidates.forEach(({ reading }, idx) =>
      out.push({ key: `R${r.round}.${idx}`, round: r.round, source: "history", reading }),
    );
  }

  // Tests `completed`, never `round_axis.live`: a stopped run's live rows stand in for its round too.
  const liveRound = roundOf(dash);
  if (dash && liveRound != null && !dash.round_axis.completed.includes(liveRound)) {
    liveCandidates(dash).forEach(({ reading }, idx) =>
      out.push({ key: `R${liveRound}.${idx}`, round: liveRound, source: "inflight", reading }),
    );
  }

  return out;
}

// The first row the arm stands on: the round it was first walked in.
export function rowAt(dash: ServedDashboard | null, key: string): ArmRow | null {
  return roundCandidates(dash).find(({ reading }) => reading.arm.key === key) ?? null;
}

export function groupByRound(rows: ArmRow[]): RoundCandidates {
  const map: RoundCandidates = new Map();
  for (const row of rows) {
    const bucket = map.get(row.round);
    if (bucket) bucket.push(row);
    else map.set(row.round, [row]);
  }
  return map;
}
