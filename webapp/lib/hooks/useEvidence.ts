import { evidenceRead } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";
import type { ReadResult } from "@/lib/read-cache";

export type EvidenceAsk = Parameters<typeof evidenceRead>[1];

// `live`: a measured course is attached, served (`producer.attached`, a course entry's
// `state.attached`); the feed moves the read only then.
export function useEvidence(subjects: readonly string[], live: boolean, ask: EvidenceAsk = {}): ReadResult<Evidence> {
  return useRead(subjects.length > 0 ? evidenceRead(subjects, ask, live) : null);
}
