import type { ServedArm, ValidationFailure } from "@/lib/api/types";

export interface CandidateVerdict {
  // `""` when absent — never a placeholder, which would read as the optimizer's words.
  changes: string;
  failures: ValidationFailure[];
}

export function armVerdicts(arms: readonly ServedArm[]): Map<string, CandidateVerdict> {
  return new Map(
    arms.map((a) => [
      a.reading.arm.key,
      { changes: a.reading.changes_description, failures: a.validation_failures },
    ]),
  );
}
