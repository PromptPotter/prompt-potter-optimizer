import { liveCandidate, liveCandidates, type DashboardSnapshot } from "@/lib/poll";
import type { ArmName, ValidationFailure } from "@/lib/api/types";
import type { ArmRow, RoundResult, SampleRow } from "@/lib/types";

function liveSamplesFor(dash: DashboardSnapshot | null, arm: ArmName): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, arm.key);
  if (!c) return out;
  const { round, key: candidate_id } = arm;
  c.samples.forEach((s) => {
    out.push({
      key: `${round}|${candidate_id}|${s.position}`,
      round,
      candidate_id,
      position: s.position,
      sample_key: s.sample_key,
      status: s.status,
      cached: s.cached,
      query: s.query,
      predicted: s.predicted,
      ground_truth_text: s.ground_truth_text,
    });
  });
  return out;
}

// Served graded (`cycle_reads.py::served_round`): "UNSC" is not a MISS, and nothing here re-decides it.
export function historicalSamplesFor(
  roundDoc: RoundResult | null,
  round: number,
  candidate_id: string,
): SampleRow[] {
  const rows = roundDoc?.cells[candidate_id];
  if (!rows) return [];
  return rows.map((s) => ({
    key: `${round}|${candidate_id}|${s.position}`,
    round,
    candidate_id,
    position: s.position,
    sample_key: s.sample_key,
    status: s.status,
    cached: s.cached,
    query: s.query,
    predicted: s.predicted,
    ground_truth_text: s.ground_truth_text,
  }));
}

export function samplesForRow(
  row: ArmRow,
  dash: DashboardSnapshot | null,
  doc: RoundResult | null,
): SampleRow[] {
  const arm = row.reading.arm;
  return row.source === "inflight"
    ? liveSamplesFor(dash, arm)
    : historicalSamplesFor(doc, arm.round, arm.key);
}

export interface CandidateVerdict {
  // `""` when absent — never a placeholder, which would read as the optimizer's words.
  changes: string;
  failures: ValidationFailure[];
}

export function candidateVerdicts(doc: RoundResult | null): Map<string, CandidateVerdict> {
  const out = new Map<string, CandidateVerdict>();
  if (!doc) return out;
  for (const { reading } of doc.scoreboard) {
    const arm = doc.arms[reading.arm.key];
    if (!arm) continue;
    out.set(reading.arm.key, {
      changes: reading.changes_description,
      failures: arm.validation_failures,
    });
  }
  return out;
}

export function liveCandidateVerdicts(dash: DashboardSnapshot | null): Map<string, CandidateVerdict> {
  return new Map(
    liveCandidates(dash).map((c) => [
      c.reading.arm.key,
      { changes: c.reading.changes_description, failures: c.validation_failures },
    ]),
  );
}
