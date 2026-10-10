"use client";

import { evidenceRead } from "@/lib/api/reads";
import type { Evidence } from "@/lib/api/types";
import { useRead } from "@/lib/hooks/useRead";

export interface EvidenceRead {
  evidence: Evidence | null;
  loading: boolean;
  error: string | null;
  invalidMetric: string | null;
}

export type EvidenceAsk = NonNullable<Parameters<typeof evidenceRead>[1]>;

export function useEvidence(
  subjects: readonly string[],
  intervalMs: number | null,
  ask: EvidenceAsk = {},
): EvidenceRead {
  const read = useRead(subjects.length > 0 ? evidenceRead(subjects, ask) : null, {
    survive: "invalid",
    intervalMs: intervalMs ?? undefined,
  });

  if (read.status === "ready") {
    return { evidence: read.data, loading: false, error: null, invalidMetric: null };
  }
  if (read.status !== "failed") {
    return { evidence: null, loading: read.status === "loading", error: null, invalidMetric: null };
  }
  if (read.failure.kind === "invalid" && read.kept !== null) {
    return { evidence: read.kept, loading: false, error: null, invalidMetric: read.failure.message };
  }
  return { evidence: null, loading: false, error: read.failure.message, invalidMetric: null };
}
