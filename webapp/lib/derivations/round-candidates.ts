import { liveCandidates, roundOf, type DashboardSnapshot } from "@/lib/poll";
import type { ArmPointer, ArmReading } from "@/lib/api/types";
import type { ArmRow, RoundCandidates, RoundResult } from "@/lib/types";

export function scoreboardReading(doc: RoundResult | null, candidateId: string): ArmReading | null {
  return doc?.scoreboard.find((r) => r.reading.arm.candidate_id === candidateId)?.reading ?? null;
}

export function roundCandidates(dash: DashboardSnapshot | null): ArmRow[] {
  const out: ArmRow[] = [];

  for (const r of dash?.rounds ?? []) {
    r.candidates.forEach(({ reading }, idx) =>
      out.push({ key: `R${r.round}.${idx}`, source: "history", reading }),
    );
  }

  // Tests `completed`, never `round_axis.live`: a stopped run's live rows stand in for its round too.
  const liveRound = roundOf(dash);
  if (dash && liveRound != null && !dash.round_axis.completed.includes(liveRound)) {
    liveCandidates(dash).forEach(({ reading }, idx) =>
      out.push({ key: `R${liveRound}.${idx}`, source: "inflight", reading }),
    );
  }

  return out;
}

export function rowAt(
  dash: DashboardSnapshot | null,
  arm: Pick<ArmPointer, "round" | "label">,
): ArmRow | null {
  return (
    roundCandidates(dash).find(
      ({ reading }) => reading.arm.round === arm.round && reading.arm.label === arm.label,
    ) ?? null
  );
}

export function groupByRound(rows: ArmRow[]): RoundCandidates {
  const map: RoundCandidates = new Map();
  for (const row of rows) {
    const round = row.reading.arm.round;
    const bucket = map.get(round);
    if (bucket) bucket.push(row);
    else map.set(round, [row]);
  }
  return map;
}
