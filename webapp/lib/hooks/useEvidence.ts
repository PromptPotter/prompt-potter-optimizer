import { useEffect, useRef } from "react";
import { evidenceRead } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { LIVE_EVIDENCE_MS } from "@/lib/cadence";
import { useRead, type ReadFailure } from "@/lib/hooks/useRead";
import { invalidateReads } from "@/lib/read-cache";

interface EvidenceRead {
  evidence: Evidence | null;
  loading: boolean;
  failure: ReadFailure | null;
  invalidMetric: ReadFailure | null;
}

export type EvidenceAsk = NonNullable<Parameters<typeof evidenceRead>[1]>;

// What the measured courses are doing: `live` is the served attachment (`producer.attached` on the
// dashboard, `state.attached` on a course entry); `moved` changes when any of them filed a round,
// scored a cell or changed phase.
export interface EvidencePulse {
  live: boolean;
  moved: string;
}

export function useEvidence(
  subjects: readonly string[],
  pulse: EvidencePulse,
  ask: EvidenceAsk = {},
): EvidenceRead {
  const read = useRead(subjects.length > 0 ? evidenceRead(subjects, ask) : null, {
    survive: "invalid",
    intervalMs: pulse.live ? LIVE_EVIDENCE_MS : undefined,
  });

  // A move re-asks at once rather than on the slow beat; the mount's own first read is not a move.
  const seenRef = useRef(pulse.moved);
  useEffect(() => {
    if (seenRef.current === pulse.moved) return;
    seenRef.current = pulse.moved;
    invalidateReads("evidence");
  }, [pulse.moved]);

  if (read.status === "ready") {
    return { evidence: read.data, loading: false, failure: null, invalidMetric: null };
  }
  if (read.status !== "failed") {
    return { evidence: null, loading: read.status === "loading", failure: null, invalidMetric: null };
  }
  if (read.failure.kind === "invalid" && read.kept !== null) {
    return { evidence: read.kept, loading: false, failure: null, invalidMetric: read.failure };
  }
  return { evidence: null, loading: false, failure: read.failure, invalidMetric: null };
}
