import { liveCandidate, liveCandidates, type DashboardSnapshot } from "@/lib/poll";
import type { ArmPointer, ScoredCandidate, ValidationFailure } from "@/lib/api/types";
import type { ArmRow, RoundResult, SampleRow } from "@/lib/types";

function liveSamplesFor(dash: DashboardSnapshot | null, arm: ArmPointer): SampleRow[] {
  const out: SampleRow[] = [];
  const c = liveCandidate(dash, arm.label);
  if (!c) return out;
  const { round, candidate_id } = arm;
  c.samples.forEach((s, ord) => {
    out.push({
      key: `${round}|${arm.label}|${s.sample_id ?? `o${ord}`}`,
      round,
      candidate_id,
      sample_id: s.sample_id,
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
  const rows = roundDoc?.all_candidate_results[candidate_id];
  if (!rows) return [];
  return rows.map((s) => ({
    key: `${round}|${candidate_id}|${s.sample_id}`,
    round,
    candidate_id,
    sample_id: s.sample_id,
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
    : historicalSamplesFor(doc, arm.round, arm.candidate_id);
}

export interface CandidateVerdict {
  // `""` when absent — never a placeholder, which would read as the optimizer's words.
  changes: string;
  failures: ValidationFailure[];
}

export function candidateVerdicts(
  rows: readonly Pick<ScoredCandidate, "label" | "changes_description" | "validation_failures">[],
): Map<string, CandidateVerdict> {
  return new Map(
    rows.map((c) => [c.label, { changes: c.changes_description, failures: c.validation_failures }]),
  );
}

export function liveCandidateVerdicts(dash: DashboardSnapshot | null): Map<string, CandidateVerdict> {
  return new Map(
    liveCandidates(dash).map((c) => [
      c.reading.arm.label,
      { changes: c.reading.changes_description, failures: c.validation_failures },
    ]),
  );
}
