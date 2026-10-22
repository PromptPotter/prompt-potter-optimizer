import { liveCandidates, type DashboardSnapshot } from "@/lib/poll";
import type { ValidationFailure } from "@/lib/api/types";
import type { RoundView } from "@/lib/types";

export interface CandidateVerdict {
  // `""` when absent — never a placeholder, which would read as the optimizer's words.
  changes: string;
  failures: ValidationFailure[];
}

export function candidateVerdicts(doc: RoundView | null): Map<string, CandidateVerdict> {
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
